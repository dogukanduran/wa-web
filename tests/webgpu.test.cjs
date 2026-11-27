const assert = require('node:assert/strict');
const { test } = require('node:test');

global.requestAnimationFrame = callback => setImmediate(callback);

async function fixture(outcomes) {
  const { createCanvasDevice } = await import('../web/webgpu.mjs');
  const requests = [], devices = [];
  let unconfigured = 0, current;
  const context = {
    configure({ device }) { current = device; },
    getCurrentTexture() { return { createView() { return {}; } }; },
    unconfigure() { unconfigured++; },
  };
  const canvas = { getContext(kind) { assert.equal(kind, 'webgpu'); return context; } };
  const gpu = {
    getPreferredCanvasFormat() { return 'rgba8unorm'; },
    async requestAdapter({ powerPreference }) {
      requests.push(powerPreference);
      const outcome = outcomes.shift();
      if (outcome === 'absent') return null;
      return {
        info: { vendor: powerPreference }, features: new Set(), limits: {},
        async requestDevice() {
          let lose;
          const device = {
            destroyed: 0,
            lost: new Promise(resolve => { lose = resolve; }),
            queue: {
              submit() {},
              async onSubmittedWorkDone() {
                // Model Chromium reporting external-memory import failure
                // after queue completion, while presentation is still pending.
                if (outcome === 'lost') setImmediate(() => lose({ message: 'import failed' }));
              },
            },
            createCommandEncoder() {
              return { beginRenderPass() { return { end() {} }; }, finish() { return {}; } };
            },
            destroy() { this.destroyed++; lose({ reason: 'destroyed' }); },
          };
          devices.push(device);
          return device;
        },
      };
    },
  };
  return { run: () => createCanvasDevice(canvas, gpu), requests, devices,
    unconfigured: () => unconfigured, current: () => current };
}

test('retains the preferred device only after actual canvas presentation', async () => {
  const f = await fixture(['good']);
  const device = await f.run();
  assert.equal(device, f.devices[0]);
  assert.equal(device.destroyed, 0);
  assert.equal(f.unconfigured(), 1);
  assert.deepEqual(f.requests, ['high-performance']);
});

test('deferred import loss releases the failed device and retains a working alternate', async () => {
  const f = await fixture(['lost', 'good']);
  const device = await f.run();
  assert.deepEqual(f.requests, ['high-performance', 'low-power']);
  assert.equal(f.devices[0].destroyed, 1);
  assert.equal(device, f.devices[1]);
  assert.equal(device.destroyed, 0);
  assert.equal(f.unconfigured(), 2);
});

test('both incompatible adapters fail without retaining devices or configured canvases', async () => {
  const f = await fixture(['lost', 'lost']);
  await assert.rejects(f.run(), /import failed/);
  assert.deepEqual(f.devices.map(d => d.destroyed), [1, 1]);
  assert.equal(f.unconfigured(), 2);
});

test('unavailable preferred adapter can use a working alternate', async () => {
  const f = await fixture(['absent', 'good']);
  assert.equal(await f.run(), f.devices[0]);
  assert.deepEqual(f.requests, ['high-performance', 'low-power']);
});
