// Night Watch Showdown runner. Plays each deal twice with the first seat swapped,
// the same loop as prototypes/showdown/sim.mjs, against one extracted snapshot.
//   node showdown_sim.mjs <snapshot> <firstSeed> <deals> <out.json>
// The snapshot's own prototypes/showdown/rules.js and its default bot (playTurn level 2) are used.
import {writeFileSync} from "node:fs";
import {join, resolve} from "node:path";
import {pathToFileURL} from "node:url";

const [snapshot, fromArg, dealsArg, outFile] = process.argv.slice(2);
const from = Number(fromArg);
const deals = Number(dealsArg);
if (!snapshot || !outFile || !Number.isInteger(from) || from < 0 || !Number.isInteger(deals) || deals < 1) {
  console.error("Usage: node showdown_sim.mjs <snapshot> <firstSeed> <deals> <out.json>");
  process.exit(2);
}

const {createShowdown, playTurn} = await import(pathToFileURL(join(resolve(snapshot), "prototypes/showdown/rules.js")).href);
const tally = {games: 0, first: 0, second: 0, cowboy: 0, wizard: 0, draw: 0, reasons: {}};
const byStartingSide = {cowboy: {cowboy: 0, wizard: 0, draw: 0}, wizard: {cowboy: 0, wizard: 0, draw: 0}};
for (let seed = from; seed < from + deals; seed++) {
  for (const first of ["cowboy", "wizard"]) {
    let state = createShowdown(seed, {first});
    while (!state.result) state = playTurn(state).state;
    const winner = state.result.winner;
    tally.games += 1;
    tally.reasons[state.result.reason] = (tally.reasons[state.result.reason] ?? 0) + 1;
    if (winner === null) {
      tally.draw += 1;
      byStartingSide[first].draw += 1;
    } else {
      tally[winner] += 1;
      tally[winner === first ? "first" : "second"] += 1;
      byStartingSide[first][winner] += 1;
    }
  }
}

writeFileSync(outFile, JSON.stringify({
  mode: "showdown",
  seeds: {from, to: from + deals - 1, deals},
  bot: "playTurn level 2 (both seats)",
  ...tally,
  byStartingSide,
}, null, 2) + "\n");
