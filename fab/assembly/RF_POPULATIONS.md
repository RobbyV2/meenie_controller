# First-build RF population matrix

The four BOM/CPL packages under `out/assembly/` are mutually exclusive build
variants.  The transmission line itself is fixed at the accepted controlled-
impedance geometry; the antenna-side L2/C16 pair is intentionally not frozen
before VNA data.

| Variant | L2 | C16 | C14/L1/C15 | C17 |
| --- | --- | --- | --- | --- |
| A-4n7-0p5 | 4.7 nH C237443 | 0.5 pF C237424 | 0.8 pF / 4.7 nH / 0.5 pF | DNP |
| B-4n7-0p8 | 4.7 nH C237443 | 0.8 pF C408126 | same | DNP |
| C-3n9-DNP | 3.9 nH C337939 | DNP | same | DNP |
| D-3n9-2p4 | 3.9 nH C337939 | 2.4 pF C1864607 | same | DNP |

Do not combine these into one ambiguous BOM.  If mixed populations on a single
panel are desired, obtain written assembler acceptance and a returned unit-by-
unit variant drawing before approving placement.
