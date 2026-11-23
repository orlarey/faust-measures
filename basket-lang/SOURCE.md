# basket-lang : the programs of audio-dsl-bench

The 38 Faust programs of [audio-dsl-bench](https://github.com/sletz/audio-dsl-bench), copied
unchanged from its `dsp/` directory at commit `bd1dd8851388e646b367029697223db4ea38a284`
(23 November 2026), with the agreement of its author. That benchmark measures the same
programs written in onda, Cmajor, YDSP and by hand in C++ ; this basket measures their Faust
side only, version against version, like the first basket.

Eighteen programs were written for the benchmark. The other twenty write out examples of the
Faust distribution with their default settings, and four of them change those defaults because
they make the program trivial : `pitch_shifter` shifts by 5 semitones, `autopan` has its amount
at 1, `tapiir` has 0.05·(n+1) s delays and 0.1 tap gains, and `noise_gate` keeps its -30 dB
threshold. The four alternative writings of the benchmark (`fir32`, `hoa_decoder`, `mixer16`,
`frenchbell_x`) are left out : their Faust programs are those of `fir32c`, `hoa_decoder_x`,
`mixer16_x` and `frenchbell`.

The benchmark feeds the programs with white noise ; the harness of this basket gives them its
own input. Times compare between sessions of this basket only : a campaign is one basket.
