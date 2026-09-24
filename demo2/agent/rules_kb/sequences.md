# Winning: sequences

A sequence is 5 of one team's chips in a row - horizontally, vertically, or
diagonally. Board corners are wild and count toward any team's sequence
without ever holding a chip.

A candidate 5-in-a-row only counts as a NEW sequence if it shares at most
1 cell with cells already used by that team's previously-counted
sequences. If it would share 2 or more cells with an already-counted
sequence, it does not count, even though some of its cells are new.

`usedSequenceCells[teamId]` tracks every cell already "spent" on a counted
sequence for that team. A one-eyed jack can never remove a chip sitting on
one of that team's used-sequence cells - once a chip is locked into a
counted sequence it is permanently safe from removal.

The number of sequences needed to win depends on team count (2 teams need
more than 3 teams need, since more teams means less board contention per
team - see teamConfig.js `sequencesToWin`).
