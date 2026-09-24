# Dead cards

A regular (non-jack) card is "dead" for a player when both of its two
printed board cells are already occupied by chips (any team, including
locked corners logic aside). Jacks are never dead: two-eyed jacks can
always place somewhere as long as any open non-corner cell exists, and
one-eyed jacks can always remove as long as any removable opponent chip
exists (one not already in a counted sequence).

A player may only submit DISCARD_DEAD_CARD for a card that has zero legal
PLACE/REMOVE targets right now. Discarding a card that still has a legal
target is illegal and must be rejected. A dead-card discard is a free
bonus action, not a real turn: the player draws a replacement card and the
turn does NOT advance to the next player - they get to act again
immediately (place/remove, or discard another dead card if they have one).
The turn only advances after an actual PLACE or REMOVE move.
