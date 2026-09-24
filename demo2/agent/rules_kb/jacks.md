# Jacks are special and never printed on the board

Two-eyed jacks - JC, JD - are wild PLACE cards: they may place a chip on
any open, non-corner cell, regardless of what card is printed there.

One-eyed jacks - JH, JS - are REMOVE cards: instead of placing, they remove
one opposing team's chip from the board. They cannot remove a chip that is
already locked into a counted sequence (see sequences.md,
usedSequenceCells). One-eyed jacks can never be "dead" - removal always has
a target as long as any removable opponent chip exists.

A move's `action` field must match its card: one-eyed jacks -> REMOVE,
every other card including two-eyed jacks -> PLACE. A PLACE action
submitted with a one-eyed jack (or vice versa) is illegal and must be
rejected, not silently reinterpreted.
