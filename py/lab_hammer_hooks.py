'''
This file is for hammer hooks specific to the EE-477 Modules.
'''

import json
import os, stat
from tokenize import maybe

from hammer_vlsi.constraints import MMMCCornerType
import hammer_vlsi
from hammer_vlsi import CLIDriver, HammerToolHookAction, HierarchicalMode
import hammer_tech
from hammer_tech import Library, ExtraLibrary

from typing import Dict, Callable, Optional, List
from pathlib import Path

def fakeram_gen_macro_swaps(x: hammer_vlsi.HammerTool) -> bool:
    '''
    Generate a verilog Library which uses the BSG hard macro swapping system to swap out generated SRAMs.
    Any SRAM dimensions specified in the Hammer config files will swapped. This covers instances of:
    * bsg_mem_1rw_sync
    * bsg_mem_1rw_sync_mask_write_bit
    * bsg_mem_1rw_sync_mask_write_byte
    
    Where to use:
        post_insertion_hook for the 'register_macros' BSG Fakeram SRAM generator step.
    '''
    # If no srams, do nothing
    if not len(x.input_parameters):
        return True    
    bsg_root = x.get_setting('bsg_root')
    swap_gen_script_path = f'{bsg_root}/hard/common/bsg_mem/bsg_mem_generator.py'
    # Create generated verilog output directory
    out_dir = os.path.join(x.run_dir, 'generated_v')
    try: os.mkdir(out_dir)
    except: pass
    # Write config file for hard swap file generator: 'bsg_mem_generator'
    swap_gen_dict = {'memories':[]}
    for p in x.input_parameters:
        swap_gen_dict['memories'].append({'ports':'1rw', 'mux':1, 'type':'1sram', 'width':p.width, 'depth':p.depth})
    swap_gen_cfg = os.path.join(x.run_dir, 'swap_gen_cfg.json')
    with open(swap_gen_cfg, 'w') as fout: fout.write(json.dumps(swap_gen_dict, indent=2))
    # Combine all generated hard swap files
    hard_swap_v_name = os.path.join(out_dir, 'bsg_mem_1rw_sync_all.v')
    # Add verilog header files from BSG repo (Contains verilog macros) for 3 types of rams (no write mask, bit write mask, byte write mask)
    os.system(f'cat {bsg_root}/hard/fakeram/bsg_mem_1rw_sync_macros.vh                  > {hard_swap_v_name}')
    os.system(f'cat {bsg_root}/hard/fakeram/bsg_mem_1rw_sync_mask_write_bit_macros.vh  >> {hard_swap_v_name}')
    os.system(f'cat {bsg_root}/hard/fakeram/bsg_mem_1rw_sync_mask_write_byte_macros.vh >> {hard_swap_v_name}')
    # Generate and concatinate hard swap files for all 3 types of rams. Must use Python 2! :(
    os.system(f'python2 {swap_gen_script_path} {swap_gen_cfg} 1rw 0                    >> {hard_swap_v_name}')
    os.system(f'python2 {swap_gen_script_path} {swap_gen_cfg} 1rw 1                    >> {hard_swap_v_name}')
    os.system(f'python2 {swap_gen_script_path} {swap_gen_cfg} 1rw 8                    >> {hard_swap_v_name}')
    # Create library for hard swap verilog file. Use it for both synthesis and simulation
    x.output_libraries.append(ExtraLibrary(
        prefix=None, library=Library(
            name='macro_hard_swap', verilog_synth=hard_swap_v_name, verilog_sim=hard_swap_v_name
        )
    ))
    return True

# SYN HOOKS ###################################################################

def genus_set_derating(x: hammer_vlsi.HammerTool) -> bool:
    '''
    Set derating for Genus synthesis.
    
    Where to use:
        pre_insertion_hook for the 'syn_generic' Genus step.
    '''
    # get derating factor
    derating_factor = x.get_setting('syn.derating_factor')
    if derating_factor is None:
        derating_factor = 0.03
    less = 1.0 - derating_factor
    more = 1.0 + derating_factor

    # get all corners
    corners = x.get_mmmc_corners()
    if corners:
        for corner in corners:
            type_ = "setup" if corner.type is MMMCCornerType.Setup else \
                "hold" if corner.type is MMMCCornerType.Hold else \
                "extra" if corner.type is MMMCCornerType.Extra else None
            if type_ is None:
                raise ValueError(f"Unsupported MMMCCornerType for corner {corner.name}: {corner.type}")
            x.verbose_append(f'set_timing_derate -delay_corner {corner.name}.{type_}_delay -early {less}')
            x.verbose_append(f'set_timing_derate -delay_corner {corner.name}.{type_}_delay -late {more}')
    return True

# DFT HOOKS ###################################################################

def _dft_enabled(x: hammer_vlsi.HammerTool) -> bool:
    try:
        return bool(x.get_setting('dft.enable'))
    except Exception:
        return False

def genus_dft_macros(x: hammer_vlsi.HammerTool) -> bool:
    '''
    Pass DFT_EN macro to Genus so the scan-port `ifdef block in chip_top.sv
    is picked up during synthesis. Genus has no set_db attribute for SV
    `defines in this version, so write a tiny SV file containing the define
    and prepend it to input_files (which init_environment will compile).

    Where to use:
        pre_insertion_hook for the 'init_environment' Genus step.
    '''
    if not _dft_enabled(x): return True
    os.makedirs(x.run_dir, exist_ok=True)
    dft_define_sv = os.path.join(x.run_dir, '_dft_define.sv')
    with open(dft_define_sv, 'w') as f:
        f.write('`define DFT_EN\n')
    x.input_files = [dft_define_sv] + list(x.input_files)
    return True

def genus_dft_setup(x: hammer_vlsi.HammerTool) -> bool:
    '''
    Define test clock, shift enable and scan chain before generic synthesis.

    Where to use:
        pre_insertion_hook for the 'syn_generic' Genus step.
    '''
    if not _dft_enabled(x): return True
    period = x.get_setting('dft.scan_period_ns')
    clk    = x.get_setting('dft.scan_clock_port')
    se     = x.get_setting('dft.scan_en_port')
    si     = x.get_setting('dft.scan_in_port')
    so     = x.get_setting('dft.scan_out_port')

    x.verbose_append(f'define_test_clock   -name scanclk -period {period} {clk}')
    x.verbose_append(f'define_shift_enable -name se -active high {se}')
    x.verbose_append(f'define_scan_chain   -name top_chain -sdi {si} -sdo {so} -shift_enable se')
    # Allow shift enable to override clock-gating cells during shift
    x.verbose_append('set_db [current_design] .lp_clock_gating_test_signal use_shift_enable')
    # Submodule-level dft_dont_scan markings live in genus_dft_post_map: at
    # this point in the flow (pre_syn_generic) Genus has read RTL but not
    # elaborated the module hierarchy, so `module:<top>/<sub>` references
    # fail with TUI-182 even with `catch`. Setting the attribute after
    # syn_map (when the elaborated hierarchy is resolved) is reliable, and
    # still runs before fix_dft_violations / convert_to_scan / connect.
    return True

def genus_dft_post_map(x: hammer_vlsi.HammerTool) -> bool:
    '''
    After syn_map: check DFT rules, fix violations, stitch the scan chain,
    write the scanDEF for Innovus and reports for sign-off.

    Where to use:
        post_insertion_hook for the 'syn_map' Genus step.
    '''
    if not _dft_enabled(x): return True
    top = x.top_module
    clk = x.get_setting('dft.scan_clock_port')

    # Allow netlist mods on the clock/async path even though pad_ring_64 is
    # preserved. Without this, fix_dft_violations cannot insert test-point
    # muxes on the clock path that goes through the pad ring, and most
    # registers end up excluded from the scan chain. Stays false through
    # connect_scan_chains so chain stitching is also unhindered.
    x.verbose_append('set_db / .ui_respects_preserve false')

    # User-configurable scan exclusions (dft.dont_scan_instances in YAML).
    # Each entry is an instance hierarchy path relative to the top module
    # — e.g. "u_bsg_link_wrapper" for a direct child, "u_soc_top/spi_inst"
    # for a grandchild. All flops in the named instance (recursively) are
    # skipped by connect_scan_chains.
    #
    # Instance paths (not module names) are used because parameterized
    # modules get uniquified during elaboration (e.g. bsg_link_wrapper
    # becomes bsg_link_wrapper_<param_hash>), so `module:<top>/<name>`
    # silently fails with TUI-182 for any module that takes parameters.
    # `get_db hinsts` walks the instance tree using the user-written
    # instance names from the RTL, which are stable.
    #
    # This must run BEFORE check_dft_rules so fix_dft_violations doesn't
    # waste time clock-muxing flops we're about to drop. An empty result
    # from get_db is silently a no-op for set_db, but `catch` is added
    # for safety against future Genus versions changing that behaviour —
    # always verify with `grep length: build/syn-rundir/reports/<top>-DFTchains`
    # after changing the list, since silent no-ops mask real config bugs.
    try:
        dont_scan = x.get_setting('dft.dont_scan_instances') or []
    except KeyError:
        dont_scan = []
    for inst in dont_scan:
        full = f'{top}/{inst}'
        x.verbose_append(
            f'catch {{ set_db [get_db hinsts {full}] .dft_dont_scan true }}')

    x.verbose_append('check_dft_rules')
    x.verbose_append(f'fix_dft_violations -clock -async_set -async_reset -test_control se -scan_clock_pin {clk}')
    x.verbose_append('check_dft_rules -advanced')
    # syn_map produces plain DFFs; convert them to SDFF (scan) cells before
    # stitching, otherwise connect_scan_chains finds no scan-capable flops.
    x.verbose_append('convert_to_scan')
    x.verbose_append('connect_scan_chains -auto_create_chains')
    x.verbose_append('set_db / .ui_respects_preserve true')
    x.verbose_append(f'report dft_chains    > reports/{top}-DFTchains')
    x.verbose_append(f'report dft_registers > reports/{top}-DFTregs')
    x.verbose_append(f'report dft_setup     > reports/{top}-DFTsetup_final')
    x.verbose_append(f'write_scandef             > {top}-scanDEF')
    # write_dft_abstract_model needs top-level test ports; skipped because
    # scan_en/in/out are on internal buffer pins (routed through pad ring).
    # The abstract model is only needed for hierarchical ATPG, which isn't
    # in this flow.

    # Generate Modus ATPG run scripts (uses the new Modus Tcl API:
    # build_model / build_testmode / verify_test_structures /
    # build_faultmodel / create_logic_tests).
    #
    # tsmc180_scan.v ahead of the stock TSMC lib is harmless cosmetic —
    # the real TSMC SDFF cells already match Modus's GSD pattern. The
    # actual gating issue is the pinassign Genus auto-emits, which only
    # contains -SC + -ES + an internal +SE cutpoint because our scan
    # I/O ports are buried behind buffer cells, not at top-level. We
    # post-process the pinassign below to declare PAD[20..22] as the
    # external scan_en/scan_in/scan_out so Modus traces the chain
    # through the pad ring (gives ~97% coverage instead of 0.96%).
    project_cfg = os.path.join(os.path.dirname(os.path.dirname(x.run_dir)), 'cfg')
    scan_lib    = os.path.join(project_cfg, 'tsmc180_scan.v')
    tcb_lib = "/home/lab.apps/vlsiapps/kits/tsmc/N180RF/core_cell_library/7-track/tcb018gbwp7t_290a/TSMCHOME/digital/Front_End/verilog/tcb018gbwp7t_270a/tcb018gbwp7t.v"
    tpd_lib = "/home/lab.apps/vlsiapps/kits/tsmc/N180RF/Standard_IO/tpd018nv_280a/TSMCHOME/digital/Front_End/verilog/tpd018nv_260a/tpd018nv.v"
    x.verbose_append(f'write_dft_atpg -directory atpg_setup -library {{{scan_lib} {tcb_lib} {tpd_lib}}} -force')

    # Overwrite the auto-generated pinassign with the correct top-level
    # scan port assignments. Genus's auto-emitted file omits scan_in /
    # scan_out and uses an internal +SE cutpoint because the scan ports
    # are buried behind buffer cells (u_scan_*_buf), not at top-level.
    # We pre-stage the corrected pinassign here in Python and have Tcl
    # `file copy` it into place after write_dft_atpg (going through Tcl
    # `puts` would hit double-quote/bracket nesting issues).
    #
    # The actual top-level pads come from dft.atpg in dft.yml so each
    # project can pick its own scan I/O pads without modifying this hook.
    # Required fields:
    #   dft.atpg.scan_clock_pin: e.g. "PAD[0]"   (also held -ES)
    #   dft.atpg.scan_en_pin:    e.g. "PAD[20]"  (held +SE during shift)
    #   dft.atpg.scan_in_pin:    e.g. "PAD[21]"  (SI for the chain)
    #   dft.atpg.scan_out_pin:   e.g. "PAD[22]"  (SO for the chain)
    # Optional:
    #   dft.atpg.constraint_pins: ["PAD[13]"]    (async resets etc.,
    #                                             each held -SC)
    # Modus 25.10 test function codes (Modus Testmodes Guide app B):
    #   -SC = system clock held low during scan (e.g. async reset)
    #   -ES = scan/system clock starting low
    #   +SE = scan enable held high during shift
    #   SI  = scan data input  (no +/- prefix)
    #   SO  = scan data output (no +/- prefix)
    try:
        atpg_clock = x.get_setting('dft.atpg.scan_clock_pin')
        atpg_se    = x.get_setting('dft.atpg.scan_en_pin')
        atpg_si    = x.get_setting('dft.atpg.scan_in_pin')
        atpg_so    = x.get_setting('dft.atpg.scan_out_pin')
    except KeyError:
        # No ATPG pin map provided — leave Genus's pinassign as-is.
        return True
    try:
        atpg_sc = x.get_setting('dft.atpg.constraint_pins') or []
    except KeyError:
        atpg_sc = []

    pinassign_src = os.path.join(x.run_dir, '_atpg_pinassign.txt')
    with open(pinassign_src, 'w') as f:
        for pin in atpg_sc:
            f.write(f'assign pin={pin} test_function= -SC;\n')
        f.write(f'assign pin={atpg_clock} test_function= -ES;\n')
        f.write(f'assign pin={atpg_se}    test_function= +SE;\n')
        f.write(f'assign pin={atpg_si}    test_function= SI;\n')
        f.write(f'assign pin={atpg_so}    test_function= SO;\n')
    pinassign_dst = f'atpg_setup/{top}.FULLSCAN.pinassign'
    x.verbose_append(f'file copy -force {pinassign_src} {pinassign_dst}')
    return True

def innovus_read_scandef(x: hammer_vlsi.HammerTool) -> bool:
    '''
    Read the pre-route scanDEF emitted by Genus so Innovus can reorder
    the chain during placement for routability.

    Where to use:
        post_insertion_hook for the 'init_design' Innovus PAR step.
    '''
    if not _dft_enabled(x): return True
    # Genus run dir lives next to par run dir under build/
    syn_rundir = os.path.join(os.path.dirname(x.run_dir), 'syn-rundir')
    scandef    = os.path.join(syn_rundir, f'{x.top_module}-scanDEF')
    x.verbose_append(f'read_def {scandef}')
    return True

def innovus_write_scandef_postroute(x: hammer_vlsi.HammerTool) -> bool:
    '''
    Write the post-route scanDEF reflecting Innovus's reordered chain.
    This is the file consumed by ATPG (Modus / Tessent / TestMAX).

    Where to use:
        post_insertion_hook for the 'route_design' Innovus PAR step.
    '''
    if not _dft_enabled(x): return True
    x.verbose_append(f'write_def -scan_chain {x.top_module}.postroute.scandef')
    return True

# PAR HOOKS ###################################################################

def innovus_gen_magic_view_script(x: hammer_vlsi.HammerTool) -> bool:
    '''
    Generate a script to open the output GDS in MAGIC.
    
    Where to use:
        post_insertion_hook for the 'write_design' Innovus PAR step.
    '''
    bash_script = os.path.join(x.generated_scripts_dir, 'magic_open_chip')
    magic_bin = x.get_setting('drc.magic.magic_bin')
    magic_rc = x.get_setting('drc.magic.rcfile')
    os.makedirs(x.generated_scripts_dir, exist_ok=True)
    with open(bash_script, 'w') as fout:
        fout.write('#!/bin/bash\n')
        fout.write(f'{magic_bin} -d XR -rcfile {magic_rc} {x.output_gds_filename} &\n')
        fout.write('echo \necho "### MAGIC launched in the background."\n')
        fout.write(f'echo "### Once the GDS has loaded, enter the command \'load {x.top_module}\' to view the top level cell."\n')
        fout.write('echo \n')
    os.chmod(bash_script, 0o755)
    x.logger.info('MAGIC chip viewer script generated.')
    return True

def innovus_gen_klayout_view_script(x: hammer_vlsi.HammerTool) -> bool:
    '''
    Generate a script to open the output GDS in Klayout. Requires manual path to Ruby libraries.
    
    Where to use:
        post_insertion_hook for the 'write_design' Innovus PAR step.
    '''
    bash_script = os.path.join(x.generated_scripts_dir, 'klayout_open_chip')
    klayout_bin = x.get_setting('klayout.klayout_bin')
    ruby_lib = x.get_setting('klayout.ruby_lib')
    layer_prop = x.get_setting('klayout.layer_properties')
    with open(bash_script, 'w') as fout:
        fout.write('#!/bin/bash\n')
        fout.write(f'export RUBYLIB={ruby_lib};\n')
        fout.write(f'{klayout_bin} -l {layer_prop} {x.output_gds_filename} &\n')
    os.chmod(bash_script, 0o755)
    x.logger.info('Klayout chip viewer script generated.')
    return True

def innovus_set_max_routing_layer(x: hammer_vlsi.HammerTool) -> bool:
    '''
    Restrict metal use during par.
    
    Where to use:
        pre_insertion_hook for the 'floorplan_design' Innovus PAR step.
    '''
    max_layer = x.get_setting('par.max_routing_layer')
    if max_layer is not None:
        x.verbose_append(f"set_db design_top_routing_layer {max_layer}")
        x.logger.info(f'Routing layer constrained to on or below {max_layer}')
        return True
    return False

# FORMAL HOOKS ################################################################

def conformal_remove_mem_src(x: hammer_vlsi.HammerTool) -> bool:
    '''
    Remove bsg_mem_1rw* source files for formal verification. This is required because we need
    the generated versions of these files in order to swap out for fakeram macro instances.

    Where to use:
        pre_insertion_hook for the 'setup_designs' Conformal step.
    '''
    x.reference_files = [s for s in x.reference_files if not s.endswith('bsg_mem_1rw_sync.v')]
    x.reference_files = [s for s in x.reference_files if not s.endswith('bsg_mem_1rw_sync_mask_write_bit.v')]
    x.reference_files = [s for s in x.reference_files if not s.endswith('bsg_mem_1rw_sync_mask_write_byte.v')]
    return True

# SIMULATION HOOKS ############################################################

def vcs_gen_trace_roms(x: hammer_vlsi.HammerTool) -> bool:
    '''
    Generates trace ROM verilog files from the list of trace files (.tr).
    
    Where to use:
        pre_insertion_step for the 'run_vcs' VCS simulation step.
    '''
    # Get trace files
    try: trace_files = x.get_setting('sim.inputs.trace_files')
    except: trace_files = []
    if not len(trace_files):
        x.logger.info('No trace files found, no trace roms will be generated.')
        return True # Return if no trace files listed
    # Get ROM generation script
    rom_gen_script = os.path.join(x.get_setting('bsg_root'), 'bsg_mem', 'bsg_ascii_to_rom.py')
    # Set up output directory
    out_dir = os.path.join(x.run_dir, 'generated_v')
    try: os.mkdir(out_dir)
    except: pass
    # Generate each file
    for t in trace_files:
        fout = os.path.join(out_dir, Path(t).stem+'_rom.v')
        open(fout, 'w').write(x.run_executable([rom_gen_script, t, Path(fout).stem]))
        x.input_files.append(fout) # Append to simulation sources
    return True