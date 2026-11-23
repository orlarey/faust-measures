# What is measured, and what compares

## A measure

The time, in nanoseconds per frame, of one program of the basket, compiled by one Faust binary with
its options and generator, in one precision, by one C++ compiler with its options, on one machine,
in one session. A session keeps every round (9 by default); the minimum is the default statistic,
the median the check.

| dimension | recorded as |
| :--- | :--- |
| the Faust binary | its `Source commit:` line, its tree hash (which survives a rebase), its own hash |
| the Faust options | `meta.faust_options` |
| the generator | `cpp` or `ocpp` |
| the precision | `float` (`-single`) or `double` |
| the Faust libraries | `meta.provenance.libraries`: their commit and local changes; every series of a session compiles with the same libraries |
| the C++ compiler and its options | `meta.cxx`, `meta.flags` |
| the machine | `meta.machine`, the host's provenance |
| the session | the directory name: the date and hour (UTC) of its start, the machine, the C++ compiler, a name |
| the basket | `basket/entries.txt` and the hash of every program |

**The generated code.** A Faust binary is built on each machine, by that machine's C++ compiler
and against its libm. The same commit may then generate slightly different code from one machine
to the next: the order of some operands, a constant folded at compilation that differs in its last
bit. Every session records the hash of the code generated for every program and series
(`meta.provenance.code`), so that a difference shows: two machines compare the same commits, not
always the same code. The basket records the hash of every program and of the `.lib` files beside
it (`meta.provenance.basket.imports`), which its imports find before the libraries.

A **series** is the measures of every program of the basket that share all the other dimensions.
Comparing two branches, two options, two C++ compilers, or one branch at two dates, is always
comparing two series.

## What compares

1. **Within a session**: any two series. Their rounds are alternated, the machine is the same,
   the ratio is the most reliable one.
2. **Across sessions, on one machine**: only through a series measured in both, the reference.
   Every time is first divided by the reference of its own session. On the machines of the first
   sessions, with nothing else running, the same binaries measured a day apart moved by about 0.2 %
   in geometric mean, and up to a few percent on single programs.
   A cloud machine is a kind of machine, not one machine: its host may change between two sessions,
   and two of its sessions compare less well than two sessions of one physical machine.
3. **Across machines**: two ways, which answer two questions. Divided each by its own reference,
   two machines compare in gain over that reference (which version gains more, here or there).
   Divided both by the same nanoseconds, the reference of one compiler of one machine, they compare
   in absolute time (which machine and compiler run the program faster).

## The reference of a session

Every session so far measures master-dev, `R`, in cpp and in ocpp, besides the versions it compares.
The site groups the sessions in campaigns: one master-dev commit, one commit of the libraries and one
set of compared versions, one session per C++ compiler; two sessions compiled with other libraries
never share a centre. It centres every program on the geometric mean of the chosen reference in cpp
and ocpp: of each compiler, of each machine, or of one compiler for every column. The centre of one
compiler can be locked: its nanoseconds stay the unit when the campaign changes, and a measure pinned
in one campaign is placed in another at its time divided by that unit.

To be decided: a fixed standard, one Faust binary built from a tag and measured in every session
whatever it compares, so that sessions far apart in time compare through it even when master-dev has
moved.
