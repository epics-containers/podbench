#include <linux/prctl.h>
#include <sys/syscall.h>

extern long syscall(long number, ...);

__attribute__((constructor)) static void allow_ptrace(void) {
    syscall(SYS_prctl, PR_SET_PTRACER, PR_SET_PTRACER_ANY, 0L, 0L, 0L);
}
