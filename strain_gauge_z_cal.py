# Strain Gauge Z Offset Calibration Module
#
# Uses an HX711 load cell sensor at a fixed bed corner pad to calibrate
# the Z offset independently from the main probe (bltouch/crtouch).
# This module does NOT register as 'probe', allowing coexistence with bltouch.
#
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
from . import hx71x, ads1220, load_cell

CALIBRATION_MOVE_SPEED = 1.0      # mm/s - slow descent speed
CALIBRATION_STEP = 0.05           # mm - step size per iteration
FORCE_THRESHOLD_GRAMS = 20.       # grams of force to detect contact
TARE_SAMPLES = 40                 # number of samples for tare averaging
CONTACT_CONFIRM_SAMPLES = 5       # consecutive samples above threshold
RETRACT_DIST = 2.0                # mm to retract after contact
CLEARANCE_Z = 10.0                # mm Z hop for safe travel moves

class StrainGaugeZCal:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name()
        # Pad position on the bed (machine coordinates)
        self.pad_x = config.getfloat('pad_x', 0.)
        self.pad_y = config.getfloat('pad_y', 5.)
        # Z calibration parameters
        self.z_start_height = config.getfloat('z_start_height', 5.)
        self.z_min_position = config.getfloat('z_min_position', -2.)
        self.force_threshold = config.getfloat('force_threshold',
                                               FORCE_THRESHOLD_GRAMS)
        self.step_size = config.getfloat('step_size', CALIBRATION_STEP)
        self.speed = config.getfloat('speed', CALIBRATION_MOVE_SPEED)
        self.retract_dist = config.getfloat('retract_dist', RETRACT_DIST)
        self.clearance_z = config.getfloat('clearance_z', CLEARANCE_Z)
        self.travel_speed = config.getfloat('travel_speed', 80.)
        # Target probe section to save z_offset to (usually bltouch)
        self.target_probe_section = config.get('target_probe_section',
                                               'bltouch')
        # Initialize the HX711 sensor and LoadCell (standalone)
        sensors = {}
        sensors.update(hx71x.HX71X_SENSOR_TYPES)
        sensors.update(ads1220.ADS1220_SENSOR_TYPE)
        sensor_class = config.getchoice('sensor_type', sensors)
        self.sensor = sensor_class(config)
        self.load_cell = load_cell.LoadCell(config, self.sensor)
        # Register gcode commands
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command(
            'STRAIN_GAUGE_CALIBRATE_Z', self.cmd_CALIBRAR_Z_OFFSET,
            desc="Calibrate Z offset using strain gauge at bed corner pad")
        self.gcode.register_command(
            'STRAIN_GAUGE_TARE', self.cmd_STRAIN_GAUGE_TARE,
            desc="Tare the strain gauge sensor")
        self.gcode.register_command(
            'STRAIN_GAUGE_READ', self.cmd_STRAIN_GAUGE_READ,
            desc="Read current strain gauge force")
        logging.info("strain_gauge_z_cal: initialized (pad at X=%.1f Y=%.1f)"
                     % (self.pad_x, self.pad_y))

    def _get_toolhead(self):
        return self.printer.lookup_object('toolhead')

    def _check_homed(self):
        toolhead = self._get_toolhead()
        curtime = self.printer.get_reactor().monotonic()
        kin_status = toolhead.get_kinematics().get_status(curtime)
        if 'x' not in kin_status['homed_axes'] or \
           'y' not in kin_status['homed_axes'] or \
           'z' not in kin_status['homed_axes']:
            return False
        return True

    def _tare_sensor(self, gcmd):
        """Tare the load cell by averaging readings with no applied force."""
        gcmd.respond_info("Taring strain gauge sensor...")
        try:
            tare_counts = self.load_cell.avg_counts(TARE_SAMPLES)
        except Exception as e:
            raise gcmd.error("Tare failed: %s" % str(e))
        self.load_cell.tare(tare_counts)
        pct = self.load_cell.counts_to_percent(tare_counts)
        gcmd.respond_info("Tare complete: counts=%d (%.2f%%)" %
                          (int(tare_counts), pct))
        return tare_counts

    def _read_force_grams(self):
        """Read current force in grams from the load cell."""
        if not self.load_cell.is_calibrated():
            return None
        if not self.load_cell.is_tared():
            return None
        try:
            counts = self.load_cell.avg_counts(5)
            return self.load_cell.counts_to_grams(counts)
        except Exception:
            return None

    def _probe_z_with_force(self, gcmd):
        """Lower Z in small steps while monitoring force.
        Returns the Z position where contact was detected."""
        toolhead = self._get_toolhead()
        reactor = self.printer.get_reactor()
        gcmd.respond_info(
            "Starting Z probe descent (threshold=%.1fg, step=%.3fmm)..."
            % (self.force_threshold, self.step_size))
        contact_count = 0
        while True:
            curpos = toolhead.get_position()
            if curpos[2] <= self.z_min_position:
                raise gcmd.error(
                    "Z min position (%.2f) reached without contact!"
                    % self.z_min_position)
            # Move down one step
            next_z = curpos[2] - self.step_size
            toolhead.manual_move([None, None, next_z], self.speed)
            toolhead.wait_moves()
            # Small pause for the sensor to settle
            reactor.pause(reactor.monotonic() + 0.05)
            # Read force
            force = self._read_force_grams()
            if force is None:
                gcmd.respond_info("Warning: could not read force, continuing...")
                contact_count = 0
                continue
            if abs(force) >= self.force_threshold:
                contact_count += 1
                if contact_count >= CONTACT_CONFIRM_SAMPLES:
                    contact_z = toolhead.get_position()[2]
                    gcmd.respond_info(
                        "Contact detected! Force=%.1fg at Z=%.4f"
                        % (force, contact_z))
                    return contact_z
            else:
                contact_count = 0

    cmd_CALIBRAR_Z_OFFSET_help = ("Calibrate Z offset using strain gauge "
                                  "at bed corner pad")
    def cmd_CALIBRAR_Z_OFFSET(self, gcmd):
        # Check if printer is homed
        if not self._check_homed():
            raise gcmd.error("Printer must be homed first! Run G28")
        toolhead = self._get_toolhead()
        # Step 1: Move to safe Z height
        gcmd.respond_info("=== Z OFFSET CALIBRATION (Strain Gauge) ===")
        curpos = toolhead.get_position()
        safe_z = max(curpos[2], self.clearance_z)
        toolhead.manual_move([None, None, safe_z], 10.)
        toolhead.wait_moves()
        # Step 2: Move to pad XY position
        gcmd.respond_info("Moving to strain gauge pad at X=%.1f Y=%.1f..."
                          % (self.pad_x, self.pad_y))
        toolhead.manual_move([self.pad_x, self.pad_y, None],
                             self.travel_speed)
        toolhead.wait_moves()
        # Step 3: Move to start Z height
        toolhead.manual_move([None, None, self.z_start_height], 10.)
        toolhead.wait_moves()
        # Step 4: Tare the sensor (zero with no contact)
        self._tare_sensor(gcmd)
        # Step 5: Probe Z with force detection
        contact_z = self._probe_z_with_force(gcmd)
        # Step 6: Retract
        retract_z = contact_z + self.retract_dist
        toolhead.manual_move([None, None, retract_z], 10.)
        toolhead.wait_moves()
        # Step 7: Calculate and save Z offset
        # After homing with bltouch, Z=0 is where the bltouch triggered
        # minus the current z_offset. If the nozzle touches the bed at
        # contact_z, the actual bed is at that Z position.
        # The correct z_offset adjustment:
        #   new_z_offset = current_z_offset - contact_z
        # If contact_z < 0 (nozzle went below Z=0), offset increases.
        # If contact_z > 0 (nozzle was above Z=0), offset decreases.
        try:
            probe = self.printer.lookup_object('probe')
            current_offsets = probe.get_offsets()
            current_z_offset = current_offsets[2]
        except Exception:
            current_z_offset = 0.0
        new_z_offset = current_z_offset - contact_z
        # bltouch z_offset must be >= 0
        if new_z_offset < 0.:
            gcmd.respond_info(
                "WARNING: Calculated z_offset is negative (%.4f). "
                "This likely means the probe triggers BELOW the nozzle, "
                "which is unusual. Setting to 0.0 and please verify manually."
                % new_z_offset)
            new_z_offset = 0.0
        gcmd.respond_info(
            "=== CALIBRATION RESULT ===\n"
            "Contact Z position: %.4f\n"
            "Previous Z offset: %.4f\n"
            "New Z offset: %.4f\n"
            "Saving to [%s] z_offset..."
            % (contact_z, current_z_offset, new_z_offset,
               self.target_probe_section))
        # Save to configfile
        configfile = self.printer.lookup_object('configfile')
        configfile.set(self.target_probe_section, 'z_offset',
                       "%.3f" % (new_z_offset,))
        gcmd.respond_info(
            "Z offset saved! Run SAVE_CONFIG to make it permanent.\n"
            "=== CALIBRATION COMPLETE ===")

    cmd_STRAIN_GAUGE_TARE_help = "Tare the strain gauge sensor"
    def cmd_STRAIN_GAUGE_TARE(self, gcmd):
        self._tare_sensor(gcmd)

    cmd_STRAIN_GAUGE_READ_help = "Read current strain gauge force"
    def cmd_STRAIN_GAUGE_READ(self, gcmd):
        if not self.load_cell.is_calibrated():
            raise gcmd.error("Load cell not calibrated! Set counts_per_gram "
                             "and reference_tare_counts in config.")
        if not self.load_cell.is_tared():
            raise gcmd.error("Load cell not tared! Run STRAIN_GAUGE_TARE")
        force = self._read_force_grams()
        if force is None:
            raise gcmd.error("Could not read force from sensor")
        counts = self.load_cell.avg_counts(5)
        gcmd.respond_info("Force: %.2f g  (raw counts: %d, tare: %d)"
                          % (force, int(counts),
                             self.load_cell.get_tare_counts()))

    def get_status(self, eventtime):
        return self.load_cell.get_status(eventtime)


def load_config(config):
    return StrainGaugeZCal(config)
