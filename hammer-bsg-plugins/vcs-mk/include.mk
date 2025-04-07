THIS_DIR := $(realpath $(shell dirname $(realpath $(lastword $(MAKEFILE_LIST)))))
REAL_OBJ_DIR = $(realpath $(OBJ_DIR))

sim-rtl \
sim-rtl-hard \
sim-syn \
sim-syn-functional \
sim-par \
sim-par-functional: sim-%: $(REAL_OBJ_DIR)/sim-%-rundir/cfg.json
	$(eval VCS_EXEC=$(shell $(THIS_DIR)/generate_sim_cmd.py --env=$(HAMMER_ENV) --dir=$(<D) --type=$* --cfg=$<))
	$(VCS_EXEC) 2>&1 | tee -i $(<D)/build.log
	-mv sdfAnnotateInfo $(<D)
	cd $(<D) ; ./simv 2>&1 | tee -i $(<D)/run.log

redo-sim-rtl \
redo-sim-rtl-hard \
redo-sim-syn \
redo-sim-syn-functional \
redo-sim-par \
redo-sim-par-functional: redo-sim-%: clean-sim-% sim-%

debug-sim-rtl \
debug-sim-rtl-hard \
debug-sim-syn \
debug-sim-syn-functional \
debug-sim-par \
debug-sim-par-functional: debug-sim-%: $(REAL_OBJ_DIR)/sim-%-rundir/cfg.json
	$(THIS_DIR)/generate_sim_cmd.py --env=$(HAMMER_ENV) --dir=$(<D) --type=$* --cfg=$<

debug-view-sim-rtl \
debug-view-sim-rtl-hard \
debug-view-sim-syn \
debug-view-sim-syn-functional \
debug-view-sim-par \
debug-view-sim-par-functional: debug-view-sim-%: $(REAL_OBJ_DIR)/sim-%-rundir/waveform.fsdb
	$(THIS_DIR)/generate_sim_cmd.py --env=$(HAMMER_ENV) --dir=$(<D) --verdi

view-sim-rtl \
view-sim-rtl-hard \
view-sim-syn \
view-sim-syn-functional \
view-sim-par \
view-sim-par-functional: view-sim-%: $(REAL_OBJ_DIR)/sim-%-rundir/waveform.fsdb
	$(eval VERDI_EXEC=$(shell $(THIS_DIR)/generate_sim_cmd.py --env=$(HAMMER_ENV) --dir=$(<D) --verdi))
	$(VERDI_EXEC) &

clean-sim-rtl \
clean-sim-rtl-hard \
clean-sim-syn \
clean-sim-syn-functional \
clean-sim-par \
clean-sim-par-functional: clean-sim-%:
	rm -rf $(OBJ_DIR)/sim-$*-rundir

# Use this to be able to disbale simulation corners (useful for simulations
# that require SDF annotations because they contain controlled combinational
# loops).
.PHONY: disable-sim
disable-sim:
	@echo "BSG-ERROR: simulation corner disabled"
	@false

$(REAL_OBJ_DIR)/sim-rtl-rundir/cfg.json: $(HAMMER_SIM_DEPENDENCIES)
	mkdir -p $(@D)
	$(HAMMER_EXEC) -e $(HAMMER_ENV) $(addprefix -p ,$(INPUT_CFGS) $(TB_CFGS)) -l hammer.log --obj_dir $(OBJ_DIR) -o $@ dump

$(REAL_OBJ_DIR)/sim-rtl-hard-rundir/cfg.json: $(SRAM_CFG) $(HAMMER_SIM_DEPENDENCIES)
	mkdir -p $(@D)
	$(HAMMER_EXEC) -e $(HAMMER_ENV) $(addprefix -p ,$(SRAM_CFG) $(INPUT_CFGS) $(TB_CFGS)) -l hammer.log --obj_dir $(OBJ_DIR) -o $@ dump

$(REAL_OBJ_DIR)/sim-syn-rundir/cfg.json \
$(REAL_OBJ_DIR)/sim-syn-functional-rundir/cfg.json: $(REAL_OBJ_DIR)/syn-rundir/syn-output-full.json $(HAMMER_SIM_DEPENDENCIES)
	mkdir -p $(@D)
	$(HAMMER_EXEC) -e $(HAMMER_ENV) -p $< $(addprefix -p ,$(TB_CFGS)) $(HAMMER_EXTRA_ARGS) -o $@ --obj_dir $(OBJ_DIR) syn-to-sim

$(REAL_OBJ_DIR)/sim-par-rundir/cfg.json \
$(REAL_OBJ_DIR)/sim-par-functional-rundir/cfg.json: $(REAL_OBJ_DIR)/par-rundir/par-output-full.json $(HAMMER_SIM_DEPENDENCIES)
	mkdir -p $(@D)
	$(HAMMER_EXEC) -e $(HAMMER_ENV) -p $< $(addprefix -p ,$(TB_CFGS)) $(HAMMER_EXTRA_ARGS) -o $@ --obj_dir $(OBJ_DIR) par-to-sim

