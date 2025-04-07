#!/usr/bin/env python3
import argparse
import json
import os
import sys
import yaml
import re


def main ():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", required=True)
    parser.add_argument("--dir", required=True)
    parser.add_argument("--verdi", action="store_true")
    parser.add_argument("--type", required="--verdi" not in sys.argv, choices=["rtl", "rtl-hard", "syn", "syn-functional", "par", "par-functional"])
    parser.add_argument("--cfg", required="--verdi" not in sys.argv)
    args = parser.parse_args()

    flags = []
    env = {}
    cfg = {}

    with open(args.env, "r") as f:
        env = yaml.safe_load(f)

    if not args.verdi:
        with open(args.cfg, "r") as f:
            cfg = json.load(f)

        # if "bsg_root" in cfg:
        #     sys.path.append(os.path.join(cfg["bsg_root"], "bsg_mem"))
        #     from bsg_ascii_to_rom import bsg_ascii_to_rom

    ### VERDI ###
    if args.verdi:
        ### Environment variables ###
        flags += [f"export SNPSLMD_LICENSE_FILE={env['synopsys.SNPSLMD_LICENSE_FILE']};"]
        flags += [f"export VCS_HOME={os.path.join(env['synopsys.synopsys_home'],'vcs',env['sim.vcs.version'])};"]
        flags += [f"export VERDI_HOME={os.path.join(env['synopsys.synopsys_home'],'verdi',env['sim.verdi.version'])};"]
        ### VERDI binary ###
        flags += [f"{os.path.join(env['synopsys.synopsys_home'],'verdi',env['sim.verdi.version'])}/bin/verdi"]
        ### Common flags ###
        flags += ["-full64", "-ssf", os.path.join(args.dir, "waveform.fsdb")]

    ### VCS ###
    else:
        ### Generate Trace Roms ###
        if "sim.inputs.trace_files" in cfg:
            for tr in cfg["sim.inputs.trace_files"]:
                module_name = os.path.splitext(os.path.basename(tr))[0] + "_rom"
                out_v_file = os.path.join(args.dir, module_name + ".v")
                with open(out_v_file, "w") as  f:
                    bsg_ascii_to_rom(filename=tr, modulename=module_name, zero=0, spool=f)
                cfg["sim.inputs.tb_input_files"].append(out_v_file)
        ### Environment variables ###
        flags += [f"export SNPSLMD_LICENSE_FILE={env['synopsys.SNPSLMD_LICENSE_FILE']};"]
        flags += [f"export VCS_HOME={os.path.join(env['synopsys.synopsys_home'],'vcs',env['sim.vcs.version'])};"]
        flags += [f"export VERDI_HOME={os.path.join(env['synopsys.synopsys_home'],'verdi',env['sim.verdi.version'])};"]
        # VCS binary ###
        flags += [f"{os.path.join(env['synopsys.synopsys_home'],'vcs',env['sim.vcs.version'])}/bin/vcs"]
        ### Common flags ###
        flags += ["-full64", "-override_timescale=1ps/1ps", "-sverilog", f"-Mdir={args.dir}", f"-o {os.path.join(args.dir, 'simv')}"]
        ### Debug + waveform ###
        flags += ["-debug_access+all", "-kdb"]
        ### Design input ###
        flags += ["-top", cfg["sim.inputs.tb_name"]]
        v_files = []
        for i in cfg["sim.inputs.input_files"] + cfg["sim.inputs.tb_input_files"]:
            if i not in  v_files:
                v_files.append(i)
        if 'vlsi.technology.extra_libraries' in cfg.keys():
            for i in list(map(lambda x: x['library']['verilog sim'], cfg['vlsi.technology.extra_libraries'])):
                if i not in  v_files:
                    v_files.append(i)
        flags += v_files
        if "sim.inputs.tb_incdir" in cfg:
            flags += ["+incdir+" + i for i in cfg["sim.inputs.tb_incdir"]]
        if "sim.inputs.tb_defines" in cfg:
            flags += ["+define+" + i for i in cfg["sim.inputs.tb_defines"]]
        ### RTL SPECIFIC FLAGS ###
        if args.type == "rtl":
            pass
        ### RTL HARD SPECIFIC FLAGS ###
        elif args.type == "rtl-hard":
            # flags += ["build/tech-sky130-cache/primitives.v", "build/tech-sky130-cache/sky130_fd_sc_hd.v"]
            flags += [env['tsmc180.sim.verilog']]
            flags += ["+define+FUNCTIONAL", "+define+UNIT_DELAY"]
            flags += ["+nospecify", "+notimingchecks"]
        ### SYN FUNCTIONAL SPECIFIC FLAGS ###
        elif args.type == "syn-functional":
            #flags += ["build/tech-sky130-cache/primitives.v", "build/tech-sky130-cache/sky130_fd_sc_hd.v"]
            flags += [env['tsmc180.sim.verilog']]
            flags += ["+define+FUNCTIONAL", "+define+UNIT_DELAY"]
            flags += ["+nospecify", "+notimingchecks"]
        ### SYN SPECIFIC FLAGS ###
        elif args.type == "syn":
            # flags += ["build/tech-sky130-cache/primitives.v", "build/tech-sky130-cache/sky130_fd_sc_hd.v"]
            flags += [env['tsmc180.sim.verilog']]
            flags += ["-sdf", f"max:{cfg['sim.inputs.top_module']}:{cfg['sim.inputs.sdf_file']}"]
            flags += ["+neg_tchk", "+sdfverbose", "-negdelay"]
            flags += ["+define+FUNCTIONAL", "+define+UNIT_DELAY"]
            flags += ["+warn=noSDFCOM_UHICD", "+warn=noSDFCOM_ANICD"]
            assert len(cfg["sim.inputs.input_files"]) == 1
            if "sim.inputs.dut_path" in cfg:
                dut_path = cfg["sim.inputs.dut_path"]
                expanded_hier = get_expanded_verilog_hierarchy_modules(cfg["sim.inputs.input_files"][0])
                with open(os.path.join(args.dir, "bsg_cdc_paths.list"), "w") as f:
                    for inst,path in  expanded_hier:
                        if "bsg_SYNC_1_r_reg" in path or "bsg_SYNC_1_r_reg" in path:
                            cdc_path = dut_path + "." + path
                            print("instance { %s } { noTiming };" % cdc_path, file=f)
                flags += [f"+optconfigfile+{os.path.join(args.dir, 'bsg_cdc_paths.list')}"]
        ### PAR FUNCTIONAL SPECIFIC FLAGS ###
        elif args.type == "par-functional":
            #flags += ["build/tech-sky130-cache/primitives.v", "build/tech-sky130-cache/sky130_fd_sc_hd.v"]
            flags += [env['tsmc180.sim.verilog']]
            flags += ["+define+FUNCTIONAL", "+define+UNIT_DELAY"]
            flags += ["+nospecify", "+notimingchecks"]
        ### PAR SPECIFIC FLAGS ###
        elif args.type == "par":
            #flags += ["build/tech-sky130-cache/primitives.v", "build/tech-sky130-cache/sky130_fd_sc_hd.v"]
            flags += [env['tsmc180.sim.verilog']]
            flags += ["-sdf", f"max:{cfg['sim.inputs.top_module']}:{cfg['sim.inputs.sdf_file']}"]
            flags += ["+neg_tchk", "+sdfverbose", "-negdelay"]
            flags += ["+define+FUNCTIONAL", "+define+UNIT_DELAY"]
            flags += ["+warn=noSDFCOM_UHICD", "+warn=noSDFCOM_ANICD"]
            assert len(cfg["sim.inputs.input_files"]) == 1
            if "sim.inputs.dut_path" in cfg:
                dut_path = cfg["sim.inputs.dut_path"]
                expanded_hier = get_expanded_verilog_hierarchy_modules(cfg["sim.inputs.input_files"][0])
                with open(os.path.join(args.dir, "bsg_cdc_paths.list"), "w") as f:
                    for inst,path in  expanded_hier:
                        if "bsg_SYNC_1_r_reg" in path or "bsg_SYNC_1_r_reg" in path:
                            cdc_path = dut_path + "." + path
                            print("instance { %s } { noTiming };" % cdc_path, file=f)
                flags += [f"+optconfigfile+{os.path.join(args.dir, 'bsg_cdc_paths.list')}"]

    ### Print out the command ###
    print(" ".join(flags))
    sys.exit(0)


def get_expanded_verilog_hierarchy_modules(filename):
    modules = []
    module_children = {}
    current_module = None

    with open(filename) as f:
        for line in f:
            line = line.strip()
            if line == "" or line.startswith("//"):
                continue
            if line.startswith("endmodule"):
                assert current_module is not None, str(current_module)
                current_module = None
                continue
            while not line.endswith(";"):
                try:
                    next_line = next(f).strip()
                    if next_line == "" or next_line.startswith("//"):
                        continue
                    line += " " + next_line
                except StopIteration:
                    break
            m = re.search("module\s+([a-z_A-Z0-9]+)", line)
            if m is not None:
                assert current_module is None
                current_module = m.group(1)
                modules.append(current_module)
                module_children[current_module] = []
                continue
            m = re.search("([a-z_A-Z0-9\\\\]+)\s+([a-z_A-Z0-9.\\[\\]\\\\]+)\s*\(.*\);", line);
            if m is not None:
                assert current_module is not None, line
                module_children[current_module].append([m.group(1), m.group(2)])
                if m.group(2).startswith("\\"):
                    module_children[current_module][-1][1] += " "
                continue
    toplevel_modules = modules[:]   # shallow copy list
    for x in modules:
        for name, instance in module_children[x]:
            if name in toplevel_modules:
                toplevel_modules.remove(name)
    result = []
    def _expand_hierarchy_recursive(hierarchy_string, module):
        prefix = hierarchy_string + "." if hierarchy_string != "" else ""
        for x in module_children[module] :
            [name, instance] = x;
            if name in module_children:
                _expand_hierarchy_recursive(prefix + instance, name)
            else :
                result.append( (name, prefix+instance) )
    for x in toplevel_modules:
        _expand_hierarchy_recursive("", x)
    return result


if __name__ == "__main__":
    main()

