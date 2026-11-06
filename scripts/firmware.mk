include scripts/env.mk
include scripts/utils.mk

program_name := er-101-firmware-$(FIRMWARE_VERSION)
out_dir := $(build_dir)/$(program_name)
src_dirs := src $(asf_dir) 

includes += src \
	$(asf_dir)/avr32/drivers/intc \
	$(asf_dir)/avr32/utils \
	$(asf_dir)/avr32/utils/preprocessor \
	$(asf_dir)/common/boards \
	$(asf_dir)/common/boards/user_board \
	$(asf_dir)/common/utils \
	$(arch_dir)/$(ARCH)/config \
	$(asf_dir)/avr32/drivers/cpu/cycle_counter \
	$(asf_dir)/avr32/drivers/gpio \
	$(asf_dir)/avr32/drivers/qdec \
	$(asf_dir)/avr32/drivers/tc \
	$(asf_dir)/avr32/drivers/usart \
	$(asf_dir)/avr32/drivers/flashc \
	$(asf_dir)/common/services/clock \
	$(asf_dir)/common/services/fifo \
	$(asf_dir)/common/drivers/nvm \
	$(asf_dir)/avr32/drivers/pm \
	$(asf_dir)/avr32/drivers/scif \
	$(asf_dir)/avr32/utils/libs/dsplib/include \
	$(asf_dir)/avr32/drivers/spi \
	$(asf_dir)/avr32/drivers/pdca \
	$(asf_dir)/avr32/drivers/wdt \
	$(asf_dir)/avr32/drivers/ast

libraries :=

# Recursive search for source files, sorted so the link order (and so the image)
# does not depend on the make version or the file system.
cpp_sources := $(sort $(foreach D,$(src_dirs),$(call rwildcard,$D,*.cpp)))
c_sources := $(sort $(foreach D,$(src_dirs),$(call rwildcard,$D,*.c)))
S_sources := $(sort $(foreach D,$(src_dirs),$(call rwildcard,$D,*.S)))

objects := $(addprefix $(out_dir)/,$(c_sources:%.c=%.o) $(cpp_sources:%.cpp=%.o) $(S_sources:%.S=%.o)) 

CFLAGS += -DBUILD_PROFILE=\"$(PROFILE)\"

# version.h is generated from FIRMWARE_VERSION; src/meta.c includes it.
version_h := $(out_dir)/generated/version.h
includes += $(out_dir)/generated
LFLAGS = $(LFLAGS.$(ARCH))

all: tool-versions $(build_dir)/$(program_name).hex

# Record the toolchain in the build log so a hex can be traced to the tools that made it.
tool-versions:
	@echo $(describe_env) TOOLS
	@$(CC) --version | head -n 1
	@$(LD) -Wl,--version | head -n 1
	@$(MAKE) --version | head -n 1
	@echo "  toolchain_dir=$(toolchain_dir)"

.PHONY: all tool-versions clean addr2line

$(objects): scripts/env.mk $(version_h)

$(version_h): scripts/env.mk scripts/gen-version.sh
	@echo $(describe_env) VERSION $(FIRMWARE_VERSION)
	@sh scripts/gen-version.sh $(FIRMWARE_VERSION) $@

# Final linking of the ELF executable. The map file is kept next to it and
# tools/elfcheck.py verifies the layout before the hex is produced.
$(build_dir)/$(program_name).elf: $(objects) $(libraries) $(arch_dir)/$(ARCH)/er-101.ld
	@mkdir -p $(@D)
	@echo $(describe_env) LINK $(describe_target)
	@$(LD) $(CFLAGS) -o $@ $(objects) $(libraries) $(LFLAGS) -Wl,-Map=$(@:.elf=.map)
	@$(PYTHON) tools/elfcheck.py $@

# Strip the executable down to a memory-loadable binary and check the artifact.
$(build_dir)/$(program_name).hex: $(build_dir)/$(program_name).elf
	@echo $(describe_env) OBJCOPY $(describe_target)
	@mkdir -p $(@D)
	@$(SIZE) $<
	@$(OBJCOPY) -O ihex -R .eeprom -R .fuse -R .lock -R .signature $< $@
	@$(PYTHON) tools/hexcheck.py $@

# A failed check must not leave a hex or ELF behind that looks good.
.DELETE_ON_ERROR:

clean:
	rm -rf $(out_dir) $(program_name).*

# Look up the source file and line that corresponds to an address in the binary exe.
addr2line: $(build_dir)/$(program_name).elf
	@echo $(describe_env) Find ${ADDRESS} in $(build_dir)/$(program_name).elf
	@$(ADDR2LINE) -p -f -i -C -e $(build_dir)/$(program_name).elf -a $(ADDRESS)

include scripts/rules.mk
