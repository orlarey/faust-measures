# What is measured, and what compares

## A measure

The time, in nanoseconds per frame, of one program of the basket, compiled by one Faust binary with
its options and generator, in one precision, by one C++ compiler with its options, on one machine,
in one session. A session keeps every round (9 by default) ; the minimum is the default statistic,
the median the check.

| dimension | recorded as |
| :--- | :--- |
| the Faust binary | its `Source commit:` line, its tree hash (which survives a rebase), its own hash |
| the Faust options | `meta.faust_options` |
| the generator | `cpp` or `ocpp` |
| the precision | `float` (`-single`) or `double` |
| the C++ compiler and its options | `meta.cxx`, `meta.flags` |
| the machine | `meta.machine`, the host's provenance |
| the session | the directory name : the date and hour (UTC) of its start, the machine, the C++ compiler, a name |
| the basket | `basket/entries.txt` and the hash of every program |

A **series** is the measures of every program of the basket that share all the other dimensions.
Comparing two branches, two options, two C++ compilers, or one branch at two dates, is always
comparing two series.

## What compares

1. **Within a session** : any two series. Their rounds are alternated, the machine is the same,
   the ratio is the most reliable one.
2. **Across sessions, on one machine** : only through a series measured in both, the reference.
   Every time is first divided by the reference of its own session. On the machines of the first
   sessions, with nothing else running, the same binaries measured a day apart moved by about 0.2 %
   in geometric mean, and up to a few percent on single programs.
3. **Across machines** : never in absolute time. Two machines compare through their ratios to the
   same reference.

## The reference of a session

Every session so far measures master-dev, `R`, in cpp and in ocpp, besides the versions it compares.
The dashboard centres every program on the geometric mean of master-dev cpp and ocpp of its session.

To be decided : a fixed standard, one Faust binary built from a tag and measured in every session
whatever it compares, so that sessions far apart in time compare through it even when master-dev has
moved.
