// flush-to-zero and denormals-are-zero from the start of the program, as -ffast-math does
// at link time (crtfastmath.o) and -mdaz-ftz (clang 18 lacks the option) : the execution
// contract of the branch. x86 : FTZ (bit 15) and DAZ (bit 6) of MXCSR ; arm64 : FZ (bit 24)
// of FPCR, which flushes both the inputs and the results
#if defined(__x86_64__) || defined(__i386__)
#include <xmmintrin.h>
static const int faust_ftz_daz_init = (_mm_setcsr(_mm_getcsr() | 0x8040), 0);
#elif defined(__aarch64__)
#include <cstdint>
static int faust_ftz_daz_set()
{
    uint64_t fpcr;
    __asm__ __volatile__("mrs %0, fpcr" : "=r"(fpcr));
    __asm__ __volatile__("msr fpcr, %0" : : "r"(fpcr | (uint64_t(1) << 24)));
    return 0;
}
static const int faust_ftz_daz_init = faust_ftz_daz_set();
#endif
