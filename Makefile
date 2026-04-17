.SHELL: bash

.PHONY: all update-submodule
all: hammer/src/hammer-vlsi/technology/tsmc180/defaults.yml hammer-mentor-plugins/hammer $(CURDIR)/.pyyaml_touch

git-safe-dirs:
	git config --get-all safe.directory | grep -Fxq "*" || git config --global --add safe.directory "*"

update-submodule:
	GIT_ALLOW_PROTOCOL='file' git submodule update --recursive

hammer/src/hammer-vlsi/technology/tsmc180/defaults.yml: git-safe-dirs
	GIT_ALLOW_PROTOCOL='file' git submodule update --init --recursive hammer/src/hammer-vlsi/technology/tsmc180

hammer-mentor-plugins/hammer: git-safe-dirs
	git config --get-all safe.directory | grep -Fxq "/homes/projects/digital-vlsi-cad-files/tsmc18/hammer-mentor-plugins" \
		|| git config --global --add safe.directory /homes/projects/digital-vlsi-cad-files/tsmc18/hammer-mentor-plugins
	GIT_ALLOW_PROTOCOL='file' git submodule update --init --recursive hammer-mentor-plugins

$(CURDIR)/.pyyaml_touch: 
	pip3 install --user pyyaml
	touch $@
