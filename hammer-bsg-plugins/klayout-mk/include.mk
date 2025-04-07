# This one line causes errors elsewhere...
#THIS_DIR := $(realpath $(shell dirname $(realpath $(lastword $(MAKEFILE_LIST)))))

REAL_OBJ_DIR = $(realpath $(OBJ_DIR))

RUBYLIB := /home/projects/ee477.2025wtr/cad/klayout/bin-release/ruby/lib64/ruby
RUBYLIB := $(RUBYLIB):/home/projects/ee477.2025wtr/cad/klayout/bin-release/ruby/lib64/rubygems
RUBYLIB := $(RUBYLIB):/home/projects/ee477.2025wtr/cad/klayout/bin-release/ruby/share/ruby
export LD_LIBRARY_PATH := /home/projects/ee477.2025wtr/cad/klayout/bin-release:$(LD_LIBRARY_PATH)

KLAYOUT := RUBYLIB=$(RUBYLIB) /home/projects/ee477.2025wtr/cad/klayout/bin-release/klayout
KLAYOUT_GDS_FILE := $(firstword $(wildcard $(REAL_OBJ_DIR)/par-rundir/*.gds/))

# TODO: un hard code? Make hook?
open-klayout-gds:
	$(KLAYOUT) -l /home/projects/ee477.2025wtr/cad/pdk/sky130A/libs.tech/klayout/sky130A.lyp $(KLAYOUT_GDS_FILE)
