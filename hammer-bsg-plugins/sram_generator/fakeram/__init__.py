
import os, tempfile, subprocess
import shutil
from pathlib import Path
import json
from textwrap import dedent, indent
from unicodedata import name

from hammer_vlsi import MMMCCorner, MMMCCornerType, HammerTool, HammerToolStep, HammerSRAMGeneratorTool, SRAMParameters
from hammer_vlsi.units import VoltageValue, TemperatureValue
from hammer_tech import Library, ExtraLibrary
from typing import NamedTuple, Dict, Any, List
from abc import ABCMeta, abstractmethod

class Sky130BSGFakeramGenerator(HammerSRAMGeneratorTool):
    def tool_config_prefix(self) -> str:
        return "sram_generator.sky130"
    
    def version_number(self, version: str) -> int:
        return 1
    
    @property
    def steps(self) -> List[HammerToolStep]:
        steps = [
            self.run_fakeram_generator,
            self.generate_all_gds,
            self.register_macros
            ]
        return self.make_steps_from_methods(steps)
    
    @property
    def macros_dir(self) -> Path:
        return Path(self.run_dir).joinpath('results')
    
    def run_fakeram_generator(self) -> bool:
        '''Configure and invoke the Fakeram SRAM generator'''
        # Check tool exists
        generator_makefile = self.get_setting('sram_generator.fakeram.fakeram_make')
        if not Path(generator_makefile).is_file():
            self.logger.error(f'The path to FakeRam generator scripts "{generator_makefile}" does not exist')
            return False
        
        # Check all rams are 1rw
        valid_rams = []
        for p in self.input_parameters:
            if p.family == '1rw': valid_rams.append(p)
            else: self.logger.error(f'FakeRram only generates 1rw SRAMs. The requested SRAM "{p.name}" is set to type "{p.family}" and will not be generated!')
        self.attr_setter('_input_parameters', valid_rams) # Update RAMs to generated
        if not len(self.input_parameters): 
            self.logger.warning('No valid configs, no SRAMs were generated')
            return True
        
        # Write config file
        cfg_file_path = os.path.join(self.run_dir, 'fakeram.cfg')
        # Copy over common parameters
        attrs = ['tech_nm', 'voltage', 'metalPrefix', 'flipPins', 'pinWidth_nm', 
                 'pinHeight_nm', 'pinPitch_nm', 'snapWidth_nm', 'snapHeight_nm',
                 'latch_last_read', 'vlogTimingCheckSignalExpansion']
        cfg = {}
        for a in attrs: cfg[a] = self.get_setting(f'sram_generator.fakeram.{a}') 
        # Write sram parameters
        cfg['srams']=[]
        for p in self.input_parameters:
            cfg['srams'].append({'name':p.name, 'width':p.width, 'depth':p.depth, 'banks':1, 'type':'ram'})
        with open(cfg_file_path, 'w') as f: f.write(json.dumps(cfg, indent=2))
        
        # Execute ram generator
        cmd = ['make', 'run', f'CONFIG={cfg_file_path}', f'OUT_DIR={self.macros_dir}']
        self.run_executable(cmd, Path(generator_makefile).parent)
        
        return True
    
    def generate_all_gds(self) -> bool:
        '''If enabled, generate GDS views of all macros from LEF views. (Using Magic)'''
        # Check if GDS generation is enabled
        try: self.gen_gds = self.get_setting('sram_generator.fakeram.gen_gds')
        except: self.gen_gds = False
        if not self.gen_gds: 
            self.logger.info('SRAM GDS generation disabled (only LEF view available)')
            return True
        # Check if there are any SRAMs to generate
        if not len(self.input_parameters):
            return True
        # Get Magic variables
        try:
            magic_bin = self.get_setting("drc.magic.magic_bin")
            magic_rc = self.get_setting("drc.magic.rcfile")
        except:
            self.logger.error('Magic not found under DRC tools, set them or disable GDS generation.')
            return False
        # Gererate a GDS for each SRAM
        for c in self.input_parameters:
            ram_dir = self.macros_dir.joinpath(c.name)
            gds_gen_script = ram_dir.joinpath('gds_gen_cfg.tcl')
            with open(gds_gen_script, 'w') as fout:
                fout.write(f'lef read {ram_dir.joinpath(c.name+".lef")}\n')
                fout.write(f'load {c.name}\n')
                fout.write(f'gds write {ram_dir.joinpath(c.name+".gds")}\n')
                fout.write('exit\n')
            
            args = [magic_bin, "-noconsole", "-dnull", "-rcfile", magic_rc, str(gds_gen_script)]
            self.run_executable(args, cwd=self.run_dir)
            self.logger.info(f'Generated GDS for Macro {c.name}.')
        return True
    
    def register_macros(self) -> bool:
        return super().generate_all_srams_and_corners()

    def generate_sram(self, params: SRAMParameters, corner: MMMCCorner) -> ExtraLibrary:
        '''Add generated SRAMs to libraries (doesn't actually generate them.)'''
        sram_name = params.name

        # Cruftilly select process corner...
        if corner.type == MMMCCornerType.Setup:
            speed_name = "slow"
            speed = "SS"
        elif corner.type == MMMCCornerType.Hold:
            speed_name = "fast"
            speed = "FF"
        elif corner.type == MMMCCornerType.Extra:
            speed_name = "typical"
            speed = "TT"   
        
        
        file_prefix = str(self.macros_dir.joinpath(sram_name).joinpath(sram_name))
        lib_file = file_prefix+'.lib'
        lef_file = file_prefix+'.lef'
        v_file = file_prefix+'.v'
        corner_d = {'nmos': speed_name, 'pmos': speed_name, 'temperature': str(corner.temp.value_in_units("C")) + " C"}
        supplies_d = {'VDD': str(corner.voltage.value_in_units("V")) + " V", 'VSS': "0 V"}
        provides_d = [{'lib_type': "sram"}]
        
        if self.gen_gds: # Library with GDS
            gds_file = file_prefix+'.gds'
            lib = Library(name=sram_name, nldm_liberty_file=lib_file, lef_file=lef_file, verilog_sim=v_file,
                          gds_file=gds_file,            
                          corner=corner_d,
                          supplies=supplies_d,
                          provides=provides_d)
        else: # Library without GDS file
            lib = Library(name=sram_name, nldm_liberty_file=lib_file, lef_file=lef_file, verilog_sim=v_file,          
                          corner=corner_d,
                          supplies=supplies_d,
                          provides=provides_d)

        extra_lib = ExtraLibrary(prefix=None, library=lib)
        return extra_lib

tool=Sky130BSGFakeramGenerator
