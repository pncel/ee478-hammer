#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
#  hammer-vlsi plugin for Cadence Tempus.
#
#  See LICENSE for licence details.

from typing import List, Dict, Optional, Callable, Tuple, Set, Any, cast

import os
import sys
import errno

from hammer_vlsi import HammerTool, HammerTimingTool, HammerToolStep, HammerToolHookAction, \
       HierarchicalMode, MMMCCornerType
from hammer_logging import HammerVLSILogging
import hammer_tech

sys.path.append(os.path.join(os.path.dirname(os.path.realpath(__file__)),"../../common"))
from tool import CadenceTool

# Notes: this plugin should only use snake_case (common UI) commands.

class Tempus(HammerTimingTool, CadenceTool):

    def tool_config_prefix(self) -> str:
        return "timing.tempus"

    @property
    def env_vars(self) -> Dict[str, str]:
        v = dict(super().env_vars)
        v["TEMPUS_BIN"] = self.get_setting("timing.tempus.tempus_bin")
        return v

    @property
    def _step_transitions(self) -> List[Tuple[str, str]]:
        """
        Private helper property to keep track of which steps we ran so that we
        can create symlinks.
        This is a list of (pre, post) steps
        """
        return self.attr_getter("__step_transitions", [])

    @_step_transitions.setter
    def _step_transitions(self, value: List[Tuple[str, str]]) -> None:
        self.attr_setter("__step_transitions", value)


    def do_pre_steps(self, first_step: HammerToolStep) -> bool:
        assert super().do_pre_steps(first_step)
        # Restart from the last checkpoint if we're not starting over.
        # Not in the dofile, must be a command-line option
        if first_step != self.first_step:
            self.append("read_db pre_{step}".format(step=first_step.name))
        return True

    def do_between_steps(self, prev: HammerToolStep, next: HammerToolStep) -> bool:
        assert super().do_between_steps(prev, next)
        # Write a checkpoint to disk.
        self.append("write_db -overwrite pre_{step}".format(step=next.name))
        # Symlink the checkpoint to latest for open_db script later.
        self.append(f"ln -sfn pre_{next.name} latest")
        self._step_transitions = self._step_transitions + [(prev.name, next.name)]
        return True

    def do_post_steps(self) -> bool:
        assert super().do_post_steps()
        # Create symlinks for post_<step> to pre_<step+1> to improve usability.
        try:
            for prev, next in self._step_transitions:
                os.symlink(
                    os.path.join(self.run_dir, f"pre_{next}"), # src
                    os.path.join(self.run_dir, f"post_{prev}") # dst
                )
        except OSError as e:
            if e.errno != errno.EEXIST:
                self.logger.warning("Failed to create post_* symlinks: " + str(e))

        # Create checkpoint post_<last step>
        # TODO: this doesn't work if you're only running the very last step
        if len(self._step_transitions) > 0:
            last = "post_{step}".format(step=self._step_transitions[-1][1])
            self.append("write_db -overwrite {last}".format(last=last))
            # Symlink the database to latest for open_db script later.
            self.append(f"ln -sfn {last} latest")

        return self.run_tempus() and self.generate_open_db()

    def get_tool_hooks(self) -> List[HammerToolHookAction]:
        return [self.make_persistent_hook(tempus_global_settings)]

    @property
    def steps(self) -> List[HammerToolStep]:
        steps = [
            self.init_design,
            self.run_sta
        ]
        return self.make_steps_from_methods(steps)
    
    def generate_mmmc_script_timing(self) -> str:
        """
        Output for the mmmc.tcl script.
        Innovus (init_design) requires that the timing script be placed in a separate file.

        :return: Contents of the mmmc script.
        """
        mmmc_output = []  # type: List[str]

        def append_mmmc(cmd: str) -> None:
            self.verbose_tcl_append(cmd, mmmc_output)

        # Create an Innovus constraint mode.
        constraint_mode = "my_constraint_mode"

        sdc_files = self.generate_sdc_files()

        # Add Custom SDCs, if present.
        try: sdc_files.extend( self.get_setting('vlsi.inputs.custom_sdc_files', nullvalue=[]) )
        except KeyError: pass

        # If the post_synth_sdc script is present, use it instead of the input SDC files.
        #post_synth_sdc = self.post_synth_sdc
        #if post_synth_sdc is not None:
        #    sdc_files = [ post_synth_sdc ]
        par_dir = self.run_dir.replace("syn-rundir", "par-rundir").replace("timing-par-rundir", "par-rundir")
        par_sdc = os.path.join(par_dir, f"{self.top_module}.par.sdc")
        if os.path.isfile(par_sdc):
            sdc_files = [par_sdc]
            self.logger.info(f"Using par SDC: {par_sdc}")
        else:
            post_synth_sdc = self.post_synth_sdc
            if post_synth_sdc is not None:
                sdc_files = [post_synth_sdc]
                self.logger.info(f"Par SDC not found, using post_synth SDC: {post_synth_sdc}")
            else:
                self.logger.info("Neither par SDC nor post_synth SDC found, using generated SDC files")

        # TODO: add floorplanning SDC
        if len(sdc_files) > 0:
            sdc_files_arg = "-sdc_files [list {sdc_files}]".format(
                sdc_files=" ".join(sdc_files)
            )
        else:
            blank_sdc = os.path.join(self.run_dir, "blank.sdc")
            self.run_executable(["touch", blank_sdc])
            sdc_files_arg = "-sdc_files {{ {} }}".format(blank_sdc)
        append_mmmc("create_constraint_mode -name {name} {sdc_files_arg}".format(
            name=constraint_mode,
            sdc_files_arg=sdc_files_arg
        ))

        # Optional second constraint mode for DFT scan-shift analysis (mirrors
        # the same handling in hammer-cadence-plugins/common/tool.py). Tempus
        # then reports both functional and scan-mode timing in one run.
        scan_sdc_files = self.get_setting('vlsi.inputs.scan_sdc_files', nullvalue=[])
        scan_constraint_mode = None
        if scan_sdc_files:
            scan_constraint_mode = "scan_constraint_mode"
            append_mmmc("create_constraint_mode -name {name} -sdc_files [list {files}]".format(
                name=scan_constraint_mode,
                files=" ".join(scan_sdc_files)
            ))

        corners = self.get_mmmc_corners()  # type: List[MMMCCorner]
        # In parallel, create the delay corners
        if corners:
            setup_view_names = [] # type: List[str]
            hold_view_names = [] # type: List[str]
            extra_view_names = [] # type: List[str]
            scan_setup_view_names = [] # type: List[str]
            scan_hold_view_names = [] # type: List[str]
            scan_extra_view_names = [] # type: List[str]
            for corner in corners:
                # Setting up views for all defined corner types: setup, hold, extra
                if corner.type is MMMCCornerType.Setup:
                    corner_name = "{n}.{t}".format(n=corner.name, t="setup")
                    setup_view_names.append("{n}_view".format(n=corner_name))
                    if scan_constraint_mode:
                        scan_setup_view_names.append("{n}_scan_view".format(n=corner_name))
                elif corner.type is MMMCCornerType.Hold:
                    corner_name = "{n}.{t}".format(n=corner.name, t="hold")
                    hold_view_names.append("{n}_view".format(n=corner_name))
                    if scan_constraint_mode:
                        scan_hold_view_names.append("{n}_scan_view".format(n=corner_name))
                elif corner.type is MMMCCornerType.Extra:
                    corner_name = "{n}.{t}".format(n=corner.name, t="extra")
                    extra_view_names.append("{n}_view".format(n=corner_name))
                    if scan_constraint_mode:
                        scan_extra_view_names.append("{n}_scan_view".format(n=corner_name))
                else:
                    raise ValueError("Unsupported MMMCCornerType")

                # First, create Innovus library sets
                append_mmmc("create_library_set -name {name}_set -timing [list {list}]".format(
                    name=corner_name,
                    list=self.get_timing_libs(corner)
                ))
                # Skip opconds for now
                # Next, create Innovus timing conditions
                append_mmmc("create_timing_condition -name {name}_cond -library_sets [list {name}_set]".format(
                    name=corner_name
                ))
                # Next, create Innovus rc corners from qrc tech files
                try: captbl_arg = f'-cap_table {self.get_setting("par.inputs.cap_table_file")}'
                except KeyError: captbl_arg = ''
                append_mmmc("create_rc_corner -name {name}_rc -temperature {tempInCelsius} {qrc} {ctable}".format(
                    name=corner_name,
                    tempInCelsius=str(corner.temp.value),
                    qrc="-qrc_tech {}".format(self.get_mmmc_qrc(corner)) if self.get_mmmc_qrc(corner) != '' else '',
                    ctable = captbl_arg
                ))
                # Next, create an Innovus delay corner.
                append_mmmc(
                    "create_delay_corner -name {name}_delay -timing_condition {name}_cond -rc_corner {name}_rc".format(
                        name=corner_name
                ))
                # Next, create the analysis views
                append_mmmc("create_analysis_view -name {name}_view -delay_corner {name}_delay -constraint_mode {constraint}".format(
                    name=corner_name,
                    constraint=constraint_mode
                ))
                if scan_constraint_mode:
                    append_mmmc("create_analysis_view -name {name}_scan_view -delay_corner {name}_delay -constraint_mode {constraint}".format(
                        name=corner_name,
                        constraint=scan_constraint_mode
                    ))

            # Finally, apply the analysis view.
            # TODO: should not need to analyze extra views as well. Defaulting to hold for now (min. runtime impact).
            append_mmmc("set_analysis_view -setup {{ {setup_views} }} -hold {{ {hold_views} {extra_views} }}".format(
                setup_views=" ".join(setup_view_names + scan_setup_view_names),
                hold_views=" ".join(hold_view_names + scan_hold_view_names),
                extra_views=" ".join(extra_view_names + scan_extra_view_names)
            ))
        else:
            # First, create an Innovus library set.
            library_set_name = "my_lib_set"
            append_mmmc("create_library_set -name {name} -timing [list {list}]".format(
                name=library_set_name,
                list=self.get_timing_libs()
            ))
            # Next, create an Innovus timing condition.
            timing_condition_name = "my_timing_condition"
            append_mmmc("create_timing_condition -name {name} -library_sets [list {list}]".format(
                name=timing_condition_name,
                list=library_set_name
            ))
            # extra junk: -opcond ...
            rc_corner_name = "rc_cond"
            append_mmmc("create_rc_corner -name {name} {qrc}".format(
                name=rc_corner_name,
                qrc="-qrc_tech {}".format(self.get_qrc_tech()) if self.get_qrc_tech() != '' else ''
            ))
            # Next, create an Innovus delay corner.
            delay_corner_name = "my_delay_corner"
            append_mmmc(
                "create_delay_corner -name {name} -timing_condition {timing_cond} -rc_corner {rc}".format(
                    name=delay_corner_name,
                    timing_cond=timing_condition_name,
                    rc=rc_corner_name
                ))
            # extra junk: -rc_corner my_rc_corner_maybe_worst
            # Next, create an Innovus analysis view.
            analysis_view_name = "my_view"
            append_mmmc("create_analysis_view -name {name} -delay_corner {corner} -constraint_mode {constraint}".format(
                name=analysis_view_name, corner=delay_corner_name, constraint=constraint_mode))
            # Finally, apply the analysis view.
            # TODO: introduce different views of setup/hold and true multi-corner
            append_mmmc("set_analysis_view -setup {{ {setup_view} }} -hold {{ {hold_view} }}".format(
                setup_view=analysis_view_name,
                hold_view=analysis_view_name
            ))

        return "\n".join(mmmc_output)

    def init_design(self) -> bool:
        """ Load design and analysis corners """
        verbose_append = self.verbose_append

        # Read timing libraries and generate timing constraints.
        # TODO: support non-MMMC mode, use standalone SDC instead
        # TODO: read AOCV or SOCV+LVF libraries if available
        mmmc_path = os.path.join(self.run_dir, "mmmc.tcl")
        with open(mmmc_path, "w") as f:
            f.write(self.generate_mmmc_script_timing())
        verbose_append("read_mmmc {mmmc_path}".format(mmmc_path=mmmc_path))

        # Read physical LEFs (optional in Tempus)
        lef_files = self.technology.read_libs([
            hammer_tech.filters.lef_filter
        ], hammer_tech.HammerTechnologyUtils.to_plain_item)
        if self.hierarchical_mode.is_nonleaf_hierarchical():
            ilm_lefs = list(map(lambda ilm: ilm.lef, self.get_input_ilms()))
            lef_files.extend(ilm_lefs)
        verbose_append("read_physical -lef {{ {files} }}".format(
            files=" ".join(lef_files)
        ))

        # Read netlist.
        # Tempus only supports structural Verilog for the netlist; the Verilog can be optionally compressed.
        if not self.check_input_files([".v", ".v.gz"]):
            return False

        # We are switching working directories and we still need to find paths.
        abspath_input_files = list(map(lambda name: os.path.join(os.getcwd(), name), self.input_files))
        verbose_append("read_netlist {{ {files} }} -top {top}".format(
            files=" ".join(abspath_input_files),
            top=self.top_module
        ))

        if self.hierarchical_mode.is_nonleaf_hierarchical():
            # Read ILMs.
            for ilm in self.get_input_ilms():
                # Assumes that the ILM was created by Innovus (or at least the file/folder structure).
                # TODO: support non-Innovus hierarchical (read netlists, etc.)
                verbose_append("read_ilm -cell {module} -directory {dir}".format(dir=ilm.dir, module=ilm.module))

        # Read power intent
        if self.get_setting("vlsi.inputs.power_spec_mode") != "empty":
            # Setup power settings from cpf/upf
            for l in self.generate_power_spec_commands():
                verbose_append(l)

        verbose_append("init_design")

        # Read parasitics
        if self.spefs is not None: # post-P&R
            corners = self.get_mmmc_corners()
            if corners:
                rc_corners = [] # type: List[str]
                for corner in corners:
                    # Setting up views for all defined corner types: setup, hold, extra
                    if corner.type is MMMCCornerType.Setup:
                        corner_name = "{n}.{t}".format(n=corner.name, t="setup")
                    elif corner.type is MMMCCornerType.Hold:
                        corner_name = "{n}.{t}".format(n=corner.name, t="hold")
                    elif corner.type is MMMCCornerType.Extra:
                        corner_name = "{n}.{t}".format(n=corner.name, t="extra")
                    else:
                        raise ValueError("Unsupported MMMCCornerType")
                    rc_corners.append("{n}_rc".format(n=corner_name))

                # Match spefs with corners. Ordering must match (ensured here by get_mmmc_corners())!
                for (spef, rc_corner) in zip(self.spefs, rc_corners):
                    verbose_append("read_spef {spef} -rc_corner {corner}".format(spef=os.path.join(os.getcwd(), spef), corner=rc_corner))

            else:
                verbose_append("read_spef " + os.path.join(os.getcwd(), self.spefs[0]))

        # Read delay data (optional in Tempus)
        if self.sdf_file is not None:
            verbose_append("read_sdf " + os.path.join(os.getcwd(), self.sdf_file))

        # TODO: Optionally read additional DEF or OA physical data

        # Set some default analysis settings for max accuracy
        # Clock path pessimism removal
        verbose_append("set_db timing_analysis_cppr both")
        # On-chip variation analysis
        verbose_append("set_db timing_analysis_type ocv")
        # Partial path-based analysis even in graph-based analysis mode
        verbose_append("set_db timing_analysis_graph_pba_mode true")
        # Equivalent waveform model w/ waveform propagation
        #verbose_append("set_db delaycal_equivalent_waveform_model propagation")
            # BSG-STD: disabled to match the delaycal of innovus. in order to
            # use waveform propagation in our delay calculations, we would also
            # need to enable this in innovus which might cause QoR issues and
            # will likely increase runtime therefore currently we just use a
            # simpler transition model in signoff..

        # Enable signal integrity delay and glitch analysis
        if self.get_setting("timing.tempus.si_glitch"):
            verbose_append("set_db si_num_iteration 3")
            verbose_append("set_db si_delay_enable_report true")
            verbose_append("set_db si_delay_separate_on_data true")
            verbose_append("set_db si_delay_enable_logical_correlation true")
            verbose_append("set_db si_glitch_enable_report true")
            verbose_append("set_db si_enable_glitch_propagation true")
            verbose_append("set_db si_enable_glitch_overshoot_undershoot true")
            verbose_append("set_db delaycal_enable_si true")
            verbose_append("set_db timing_enable_timing_window_pessimism_removal true")
            # Check for correct noise models (ECSMN, CCSN, etc.)
            verbose_append("check_noise")

        return True

    def run_sta(self) -> bool:
        """ Run Static Timing Analysis """
        verbose_append = self.verbose_append

        # report_timing
        verbose_append("set_db timing_report_timing_header_detail_info extended")
        # Note this reports everything - setup, hold, recovery, etc.
        verbose_append(f"report_timing -retime path_slew_propagation -max_paths {self.max_paths} > {self.top_module}_timing_setup.rpt")
        verbose_append(f"report_timing -check_type hold -retime path_slew_propagation -max_paths {self.max_paths} > {self.top_module}_timing_hold.rpt")
        # Full unconstrained report annotated with path exceptions.
        # `-path_exceptions all` tags each path with:
        #   * "Applied exceptions"  — set_false_path / set_max_delay actively in effect
        #   * "Ignored exceptions"  — exception exists but Tempus's hold/uncons machinery
        #     didn't apply it (still honored by Innovus at routing)
        #   * No annotation         — path has NO exception at all (the real concerns)
        verbose_append(f"report_timing -unconstrained -debug unconstrained -path_exceptions all -max_paths {self.max_paths} > {self.top_module}_timing_unconstrained.rpt")
        # When a second constraint mode (scan_constraint_mode) is active, the
        # combined unconstrained report above mixes functional and scan-shift
        # paths.  Emit two per-mode variants so users can triage each in
        # isolation.  Compute the view-name lists in Python from the MMMC
        # corners (matches the naming convention in common/tool.py:
        # functional = "<corner>.<type>_view", scan = "<corner>.<type>_scan_view").
        if self.get_setting('vlsi.inputs.scan_sdc_files', nullvalue=[]):
            func_views = []
            scan_views = []
            for corner in self.get_mmmc_corners():
                if corner.type is MMMCCornerType.Setup:
                    base = f"{corner.name}.setup"
                elif corner.type is MMMCCornerType.Hold:
                    base = f"{corner.name}.hold"
                elif corner.type is MMMCCornerType.Extra:
                    base = f"{corner.name}.extra"
                else:
                    continue
                func_views.append(f"{base}_view")
                scan_views.append(f"{base}_scan_view")
            fv = " ".join(func_views)
            sv = " ".join(scan_views)
            verbose_append(f"report_timing -unconstrained -debug unconstrained -path_exceptions all -view {{{fv}}} -max_paths {self.max_paths} > {self.top_module}_timing_unconstrained_func.rpt")
            verbose_append(f"report_timing -unconstrained -debug unconstrained -path_exceptions all -view {{{sv}}} -max_paths {self.max_paths} > {self.top_module}_timing_unconstrained_scan.rpt")
        # Focused report: only paths ending at register D pins (real data setup
        # endpoints).  Filters out the SE / CDN / SDN / CP endpoints that dominate
        # the full report as Tempus reporting noise (scan_en distribution,
        # synchronized async-reset fanout, clock-tree PULSE_WIDTH).
        verbose_append(f"report_timing -unconstrained -debug unconstrained -path_exceptions all -to [get_pins -hier -filter {{name == D}} -of_objects [all_registers]] -max_paths {self.max_paths} > {self.top_module}_timing_unconstrained_data_only.rpt")
        # Report paths in the "default" path group.  Tempus groups paths by
        # check type; CLOCK-to-CLOCK set_max_delay paths (e.g. dn_clk -> core_clk
        # for the bsg async CDC bounds) land here instead of the normal setup/hold
        # groups, so they don't appear in chip_top_timing_setup.rpt unless we
        # specifically request the default group.
        verbose_append(f"report_timing -path_group default -max_paths {self.max_paths} > {self.top_module}_timing_default_group.rpt")
        # check_timing reports TRULY unconstrained endpoints (constraint-completeness
        # check that — unlike report_timing -unconstrained — doesn't list paths with
        # set_false_path or set_max_delay applied.  Cleanest view of "what's actually
        # missing a constraint."
        verbose_append(f"check_timing -verbose > {self.top_module}_check_timing.rpt")
        # Dead-constraints check: lists every set_false_path / set_max_delay /
        # set_multicycle_path that did NOT match any path (usually typo in pin
        # name, or cell got renamed by synthesis).  Anything here is a stale or
        # buggy SDC line worth investigating.
        verbose_append(f"report_path_exceptions -ignored > {self.top_module}_path_exceptions_ignored.rpt")
        # Clock-definition sanity check: lists every defined clock with period,
        # waveform, propagation status, uncertainty.  Verify all expected clocks
        # are declared and there are no accidentally-overlapping create_clock
        # calls or unresolved generated clocks.
        verbose_append(f"report_clocks > {self.top_module}_clocks.rpt")

        # QoR summary: per-clock, per-check-type WNS/TNS/#failing.  One screen
        # to sanity-check the whole design after any flow run.
        verbose_append(f"report_timing_summary > {self.top_module}_timing_summary.rpt")
        # All-violator catch-all: lists every violator across every check type
        # (setup, hold, recovery, removal, pulse_width, transition, capacitance,
        # fanout, clock_gating, latch_borrow) in one report.  If a category
        # doesn't appear here, you have zero violators of that type — so this
        # is the "did I miss anything" backstop.
        verbose_append(f"report_constraint -all_violators > {self.top_module}_constraint_violators.rpt")
        # Detailed reports for the less-common check types.  These are NOT
        # included in the default report_timing (which is setup only) — without
        # them a pulse-width or recovery/removal violation can hide unnoticed.
        verbose_append(f"report_timing -check_type pulse_width -max_paths {self.max_paths} > {self.top_module}_timing_pulse_width.rpt")
        verbose_append(f"report_timing -check_type recovery   -max_paths {self.max_paths} > {self.top_module}_timing_recovery.rpt")
        verbose_append(f"report_timing -check_type removal    -max_paths {self.max_paths} > {self.top_module}_timing_removal.rpt")

        if self.get_setting("timing.tempus.si_glitch"):
            # SI max/min delay
            verbose_append("report_noise -delay max -out_file max_si_delay")
            verbose_append("report_noise -delay min -out_file min_si_delay")
            # Glitch and summary histogram
            verbose_append("report_noise -out_file glitch -threshold 0.05")
            verbose_append("report_noise -histogram")

        return True

    def generate_open_db(self) -> bool:
        # Make sure that generated-scripts exists.
        generated_scripts_dir = os.path.join(self.run_dir, "generated-scripts")
        os.makedirs(generated_scripts_dir, exist_ok=True)

        # Script to open results checkpoint
        self.output.clear()
        self.create_enter_script()
        open_db_tcl = os.path.join(generated_scripts_dir, "open_db.tcl")
        with open(open_db_tcl, "w") as f:
            assert super().do_pre_steps(self.first_step)
            self.append("read_db latest")
            f.write("\n".join(self.output))
        open_db_script = os.path.join(generated_scripts_dir, "open_db")
        with open(open_db_script, "w") as f:
            f.write("""#!/bin/bash
        cd {run_dir}
        source enter
        $TEMPUS_BIN -stylus -files {open_db_tcl}
                """.format(run_dir=self.run_dir, open_db_tcl=open_db_tcl))
        os.chmod(open_db_script, 0o755)

        return True

    def run_tempus(self) -> bool:
        # Quit
        self.append("exit")

        # Write main dofile
        timing_script = os.path.join(self.run_dir, "timing.tcl")
        with open(timing_script, "w") as f:
            f.write("\n".join(self.output))

        # Build args
        # TODO: enable Signoff ECO with -tso (-eco?) option
        args = [
            self.get_setting("timing.tempus.tempus_bin"),
            "-no_gui", # no GUI
            "-stylus", # common UI
            "-files", timing_script
        ]

        # Temporarily disable colours/tag to make run output more readable.
        # TODO: think of a more elegant way to do this?
        HammerVLSILogging.enable_colour = False
        HammerVLSILogging.enable_tag = False
        self.run_executable(args, cwd=self.run_dir)
        # TODO: check for errors and deal with them
        HammerVLSILogging.enable_colour = True
        HammerVLSILogging.enable_tag = True

        # TODO: check that timing run was successful

        return True

def tempus_global_settings(ht: HammerTool) -> bool:
    """Settings that need to be reapplied at every tool invocation"""
    assert isinstance(ht, HammerTimingTool)
    assert isinstance(ht, CadenceTool)
    ht.create_enter_script()

    # Python sucks here for verbosity
    verbose_append = ht.verbose_append

    # Generic settings
    if ht.get_setting("vlsi.core.technology") == "sky130":
        verbose_append("set_message -id IMPMSMV-3001 -suppress")    # No such power domain '%s'... sky130 lib issue
        verbose_append("set_message -id TECHLIB-702  -suppress")    # No pg_pin with name '%s' has been read... sky130 lib issue
    verbose_append("set_db design_process_node {}".format(ht.get_setting("vlsi.core.node")))
    verbose_append("set_multi_cpu_usage -local_cpu {}".format(ht.get_setting("vlsi.core.max_threads")))

    return True

tool = Tempus
