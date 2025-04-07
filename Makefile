.SHELL: bash

.PHONY: all update-submodule
all: hammer/src/hammer-vlsi/technology/tsmc180/defaults.yml hammer-mentor-plugins/hammer $(CURDIR)/.pyyaml_touch

update-submodule:
	GIT_ALLOW_PROTOCOL='file' git submodule update --recursive

hammer/src/hammer-vlsi/technology/tsmc180/defaults.yml:
	git config --get-all safe.directory | grep -Fxq "/home/projects/ee478.2025spr/hammer_vlsi_technology_tsmc180" \
		|| git config --global --add safe.directory /home/projects/ee478.2025spr/hammer_vlsi_technology_tsmc180
	GIT_ALLOW_PROTOCOL='file' git submodule update --init --recursive hammer/src/hammer-vlsi/technology/tsmc180

hammer-mentor-plugins/hammer:
	git config --get-all safe.directory | grep -Fxq "/home/projects/ee478.2025spr/hammer-mentor-plugins" \
		|| git config --global --add safe.directory /home/projects/ee478.2025spr/hammer-mentor-plugins
	GIT_ALLOW_PROTOCOL='file' git submodule update --init --recursive hammer-mentor-plugins

$(CURDIR)/.pyyaml_touch: 
	pip3 install --user pyyaml
	touch $@
