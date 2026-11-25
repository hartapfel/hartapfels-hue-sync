/* Older ARM glibc walks the dynamic and PLT relocations as one range
   during eager binding. Keep the unwind index outside that range. */
SECTIONS { .rel.plt : { *(.rel.plt) *(.rel.iplt) } }
INSERT AFTER .rel.dyn;
