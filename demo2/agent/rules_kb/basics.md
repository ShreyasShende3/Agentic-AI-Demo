# Board and cards

The board is 10x10. Each of the 4 corners is a WILD cell: free for every
team simultaneously, never has a chip placed on it, and counts toward a
sequence passing through it for whichever team is checking.

Every non-corner cell shows a card rank+suit (e.g. "7H", "TD", "AS"). Each
of the 48 non-jack cards appears on exactly 2 cells on the board.

Card codes are 2 characters: rank then suit. Ranks: A,2-9,T(=ten),J,Q,K.
Suits: S(spade), H(heart), D(diamond), C(club).

# Turn structure

A player's move: PLACE a chip on one of the two cells matching a regular
card in hand (or any open non-corner cell for a two-eyed jack), or REMOVE
an opponent chip with a one-eyed jack, or DISCARD_DEAD_CARD if a card in
hand has no legal target left. After a valid move the player draws back up
to their hand size. Turn order interleaves players across teams.
