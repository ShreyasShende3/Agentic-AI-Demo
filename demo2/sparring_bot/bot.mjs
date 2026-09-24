// Legal-move-only sparring opponent for the bug-hunting agent demo.
//
// Reads sequence-game's rules engine READ-ONLY via a relative import so the
// bot's move choices can never drift from the real rules (same guarantee
// legalMoves.js gives the game's own heuristic bot). Nothing in
// ../../sequence-game is ever written to.
//
// Usage: node bot.mjs <roomCode> [wsUrl]
//   - if roomCode is "NEW", creates a room and prints its code so the
//     Python agent can join it.

import { WebSocket } from 'ws';
import { enumerateLegalMoves } from '../../sequence-game/shared/engine/legalMoves.js';

const roomCodeArg = process.argv[2] || 'NEW';
const wsUrl = process.argv[3] || 'ws://localhost:3000';
const BOT_NAME = 'SparringBot';

const ws = new WebSocket(wsUrl);
let playerId = null;
let roomCode = null;
let latestState = null;

function send(type, payload) {
  ws.send(JSON.stringify({ type, payload }));
}

// BUG FOUND (2026-09-15, this demo's first run): legalMoves.js's own JSDoc
// says it accepts "canonical GameState (or a same-shaped server snapshot)",
// but rules.js's REMOVE-action branch calls
// usedSequenceCells[team].has(...) - which throws once a team's entry has
// gone over the wire, since room.js's getSnapshotFor() redacts every
// usedSequenceCells Set to a plain Array for JSON serialization
// (Set.has exists, Array.has does not). Reproduce: any one-eyed-jack
// legality check, computed off a network snapshot, after ANY team has
// completed at least one sequence. Never modifying sequence-game itself
// (per instructions) - working around it here, client-side, since a real
// bot/UI built against the WS protocol would hit exactly this.
function toLegalMovesCompatibleState(snapshot) {
  const usedSequenceCells = {};
  for (const [teamId, cells] of Object.entries(snapshot.usedSequenceCells || {})) {
    // cellSet entries are already "row,col" strings (sequenceDetector.js's
    // cellKey convention) - Array.from() in getSnapshotFor just unwraps the
    // Set, it doesn't reshape the entries.
    usedSequenceCells[teamId] = Array.isArray(cells) ? new Set(cells) : cells;
  }
  return { ...snapshot, usedSequenceCells };
}

function pickMove(state) {
  const legal = enumerateLegalMoves(toLegalMovesCompatibleState(state), playerId);
  if (legal.length === 0) return null;
  return legal[Math.floor(Math.random() * legal.length)];
}

function maybePlay() {
  if (!latestState || latestState.status !== 'in_progress') return;
  const myTurn = latestState.turnOrder[latestState.currentPlayerIndex] === playerId;
  if (!myTurn) return;
  const move = pickMove(latestState);
  if (!move) {
    console.error('[bot] no legal move found - this itself may be a bug worth flagging');
    return;
  }
  // Small delay so logs/observability traces stay readable in a live demo.
  setTimeout(() => {
    send('SUBMIT_MOVE', {
      moveIntent: {
        type: move.type,
        action: move.action,
        card: move.card,
        targetCell: move.targetCell,
        clientMoveId: `bot_${Date.now()}`,
      },
    });
  }, 400);
}

ws.on('open', () => {
  if (roomCodeArg === 'NEW') {
    send('CREATE_ROOM', { hostName: BOT_NAME });
  } else {
    send('JOIN_ROOM', { roomCode: roomCodeArg, playerName: BOT_NAME });
  }
});

ws.on('message', (raw) => {
  const msg = JSON.parse(raw.toString());
  switch (msg.type) {
    case 'ROOM_CREATED':
      playerId = msg.payload.playerId;
      roomCode = msg.payload.roomCode;
      console.log(`ROOM_CODE=${roomCode}`);
      console.log(`BOT_PLAYER_ID=${playerId}`);
      break;
    case 'ROOM_STATE':
      // Host: once a second player has joined, start the game.
      if (msg.payload.hostPlayerId === playerId && msg.payload.players.length >= 2 && msg.payload.status === 'waiting') {
        send('START_GAME', {});
      }
      break;
    case 'GAME_STARTED':
    case 'SYNC_STATE':
      latestState = msg.payload.stateSnapshot;
      maybePlay();
      break;
    case 'MOVE_APPLIED':
      latestState = msg.payload.stateSnapshot;
      maybePlay();
      break;
    case 'MOVE_REJECTED':
      console.error('[bot] own move rejected (unexpected - would indicate a legalMoves/rules drift):', msg.payload.reason);
      break;
    case 'GAME_OVER':
      console.log(`[bot] game over, winner=${msg.payload.winnerTeamId}`);
      break;
    case 'ERROR':
      console.error('[bot] server error:', msg.payload.message);
      break;
    default:
      break;
  }
});

ws.on('close', () => console.log('[bot] connection closed'));
ws.on('error', (err) => console.error('[bot] ws error:', err.message));
