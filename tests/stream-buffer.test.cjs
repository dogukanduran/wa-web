const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

// Exercise the worker's actual parser without starting a browser or importing
// private game content. Track allocations/copies rather than timing noisy CI.
function loadBuffer() {
  const costs = { allocated: 0, copied: 0 };
  class TrackedBytes extends Uint8Array {
    constructor(...args) {
      super(...args);
      if (typeof args[0] === 'number') costs.allocated += args[0];
    }
    set(source, offset) {
      costs.copied += source.length;
      return super.set(source, offset);
    }
  }
  const context = vm.createContext({ self: {}, Uint8Array: TrackedBytes });
  const source = fs.readFileSync(path.join(__dirname, '../web/worker.js'), 'utf8');
  vm.runInContext(`${source}\nglobalThis.BufferUnderTest = StreamBuffer;`, context);
  return { StreamBuffer: context.BufferUnderTest, costs };
}

function readerOf(chunks) {
  let index = 0;
  return { async read() {
    return index < chunks.length ? { value: chunks[index++], done: false } : { done: true };
  } };
}

const bytes = (...values) => Uint8Array.from(values);

test('contiguous records share their network chunk without copying', async () => {
  const { StreamBuffer, costs } = loadBuffer();
  const chunk = bytes(1, 2, 3, 4);
  const stream = new StreamBuffer(readerOf([chunk]), 4, () => {});
  const record = await stream.readExact(3);
  assert.equal(record.buffer, chunk.buffer);
  assert.deepEqual(Array.from(record), [1, 2, 3]);
  assert.deepEqual(costs, { allocated: 0, copied: 0 });
});

test('fragmented records preserve ordering and unconsumed trailing bytes', async () => {
  const { StreamBuffer } = loadBuffer();
  const progress = [];
  const stream = new StreamBuffer(readerOf([
    bytes(0, 1, 2), bytes(3), bytes(), bytes(4, 5, 6, 7), bytes(8, 9),
  ]), 10, n => progress.push(n));
  const first = await stream.readExact(2);
  const split = await stream.readExact(5);
  assert.deepEqual(Array.from(split), [2, 3, 4, 5, 6]);
  assert.deepEqual(Array.from(await stream.readExact(3)), [7, 8, 9]);
  assert.deepEqual(Array.from(first), [0, 1]);
  assert.deepEqual(Array.from(split), [2, 3, 4, 5, 6]);
  assert.deepEqual(progress, [3, 4, 4, 8, 10]);
});

test('empty reads do not fetch and work after end of stream', async () => {
  const { StreamBuffer } = loadBuffer();
  let reads = 0;
  const stream = new StreamBuffer({ async read() { reads++; return { done: true }; } }, 0, () => {});
  assert.equal((await stream.readExact(0)).length, 0);
  assert.equal(reads, 0);
  await assert.rejects(stream.readExact(1), /Unexpected end of assets.pak/);
  assert.equal((await stream.readExact(0)).length, 0);
  assert.equal(reads, 1);
});

test('truncated split records fail instead of returning incomplete bytes', async () => {
  const { StreamBuffer } = loadBuffer();
  const stream = new StreamBuffer(readerOf([bytes(1), bytes(2, 3)]), 4, () => {});
  await assert.rejects(stream.readExact(4), /Unexpected end of assets.pak/);
});

test('download failures propagate from the reader', async () => {
  const { StreamBuffer } = loadBuffer();
  const stream = new StreamBuffer({ async read() { throw new Error('connection lost'); } }, 0, () => {});
  await assert.rejects(stream.readExact(4), /connection lost/);
});

test('large fragmented assets allocate one result and copy each byte once', async () => {
  const { StreamBuffer, costs } = loadBuffer();
  const size = 8 * 1024 * 1024;
  const chunk = new Uint8Array(16 * 1024).fill(0x5a);
  const stream = new StreamBuffer(readerOf(Array(size / chunk.length).fill(chunk)), size, () => {});
  const record = await stream.readExact(size);
  assert.equal(record.length, size);
  assert.ok(record.every(value => value === 0x5a));
  assert.deepEqual(costs, { allocated: size, copied: size });
});
