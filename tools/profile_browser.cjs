// Private offline replay profiling; output belongs under ignored build/.
const fs = require('node:fs'), path = require('node:path'),
      readline = require('node:readline');
const root = path.resolve(__dirname, '..');
const {chromium} = require(
    process.env.PLAYWRIGHT_CORE_PATH ||
    path.join(root, 'build/browser-tools/node_modules/playwright-core'));
const option = (name, fallback) =>
    process.argv.find(a => a.startsWith(name + '='))?.slice(name.length + 1) ??
    fallback;
const profile = path.resolve(
    option('--profile', path.join(root, 'build/browser-profile-benchmark')));
if (!profile.startsWith(path.join(root, 'build') + path.sep))
  throw Error('Use an isolated profile under build/');
const localPython = path.join(root, '.venv/bin/python');
const python = option(
    '--python',
    process.env.PYTHON ||
        (fs.existsSync(localPython) ? localPython : 'python3'));
const replayPath = option('--replay', '');
if (!replayPath) throw Error('--replay=/path/to/owned.WAgame is required');
const out = path.resolve(option(
    '--out',
    path.join(root, 'build/evidence', 'browser-profile-' + Date.now())));
fs.mkdirSync(out, {recursive: true});
const diagnostic = process.argv.includes('--diagnostic');
const env = {...process.env};
const event = v => fs.appendFileSync(
    path.join(out, 'events.jsonl'),
    JSON.stringify({at: Date.now(), ...v}) + '\n');
(async () => {
  const context = await chromium.launchPersistentContext(profile, {
    executablePath:
        option('--chrome', process.env.CHROME_BIN || '/usr/bin/google-chrome'),
    headless: false,
    chromiumSandbox: true,
    viewport: {width: 1280, height: 960},
    args: [
      '--enable-features=Vulkan',
      ...(process.argv.includes('--x11') ? ['--ozone-platform=x11'] : [])
    ],
    env
  });
  const browser = context.browser(), cdp = await browser.newBrowserCDPSession();
  const page = await context.newPage();
  page.on(
      'console',
      m => event({type: 'console', level: m.type(), text: m.text()}));
  page.on('pageerror', e => event({type: 'error', text: String(e)}));
  page.on(
      'requestfailed',
      r => event({type: 'failed', url: r.url(), error: r.failure()}));
  page.on(
      'response',
      r => event({type: 'response', url: r.url(), status: r.status()}));
  await context.addInitScript(diag => {
    const m = window.bench = {
      raf: [],
      canvas: [],
      longTasks: [],
      heap: [],
      gpuCalls: {},
      queueDone: [],
      created: [],
      destroyed: {},
      started: performance.now()
    };
    let lr, lc, lastHeap;
    requestAnimationFrame(function tick(t) {
      if (lr !== undefined) m.raf.push(t - lr);
      lr = t;
      requestAnimationFrame(tick)
    });
    try {
      new PerformanceObserver(
          l => m.longTasks.push(...l.getEntries().map(
              e => ({start: e.startTime, duration: e.duration}))))
          .observe({type: 'longtask', buffered: true})
    } catch {
    }
    setInterval(() => {
      const n = window.Module?.HEAPU8?.buffer.byteLength;
      if (n !== undefined && n !== lastHeap) {
        m.heap.push({at: performance.now(), bytes: n});
        lastHeap = n
      }
    }, 100);
    if (window.GPUCanvasContext) {
      const get = GPUCanvasContext.prototype.getCurrentTexture;
      GPUCanvasContext.prototype.getCurrentTexture = function(...a) {
        const v = get.apply(this, a), now = performance.now();
        if (lc !== undefined) m.canvas.push(now - lc);
        lc = now;
        return v
      }
    }
    if (diag) {
      for (const [proto, names] of [
               [
                 window.GPUQueue?.prototype,
                 [
                   'submit', 'writeTexture', 'writeBuffer',
                   'onSubmittedWorkDone'
                 ]
               ],
               [
                 window.GPUDevice?.prototype,
                 [
                   'createTexture', 'createBuffer', 'createCommandEncoder',
                   'createBindGroup', 'createRenderPipeline'
                 ]
               ],
               [
                 window.GPUCommandEncoder?.prototype,
                 [
                   'finish', 'copyTextureToTexture', 'copyBufferToTexture',
                   'beginRenderPass'
                 ]
               ]]) {
        if (!proto) continue;
        for (const name of names) {
          const old = proto[name];
          proto[name] = function(...a) {
            const start = performance.now(), v = old.apply(this, a),
                  end = performance.now(),
                  c = m.gpuCalls[name] ??= {count: 0, ms: 0, bytes: 0, max: 0};
            c.count++;
            c.ms += end - start;
            c.max = Math.max(c.max, end - start);
            if (name === 'writeTexture' || name === 'writeBuffer') {
              const data = name === 'writeTexture' ? a[1] : a[2];
              c.bytes += data?.byteLength || 0
            }
            if (name === 'onSubmittedWorkDone')
              v.then(
                  () => m.queueDone.push(performance.now() - start), () => {});
            if (name === 'createBuffer' || name === 'createTexture') {
              const q = a[0];
              let bytes = q.size;
              if (name === 'createTexture') {
                const z = Array.isArray(q.size) ?
                    q.size :
                    [q.size.width, q.size.height, q.size.depthOrArrayLayers];
                bytes = (z[0] || 1) * (z[1] || 1) * (z[2] || 1) *
                    (String(q.format).includes('r8unorm') ? 1 : 4)
              }
              m.created.push({kind: name, bytes, at: end, ref: new WeakRef(v)})
            }
            return v;
          }
        }
      }
      for (const T of [window.GPUBuffer, window.GPUTexture])
        if (T) {
          const old = T.prototype.destroy;
          T.prototype.destroy = function(...a) {
            m.destroyed[T.name] = (m.destroyed[T.name] || 0) + 1;
            return old.apply(this, a)
          }
        }
    }
  }, diagnostic);
  await page.route('**/app.js*', async route => {
    const r = await route.fetch();
    let b = await r.text();
    b = b.replace(
        'Object.assign(Module.ENV, queryEnvironment(location.search));',
        'Object.assign(Module.ENV, queryEnvironment(location.search)); Module.ENV.RECOMP_GUEST_ARGS = ' +
            JSON.stringify('"C:\\WormsArmageddon\\benchmark.WAgame"') +
            ';Module.ENV.RECOMP_DDRAW_MODES="640x480x8,640x480x16,640x480x32,1280x960x8,1280x960x16,1280x960x32";Module.ENV.RECOMP_FRAME_TIMINGS="/tmp/benchframes.csv";');
    await route.fulfill({response: r, body: b})
  });
  await page.goto(option('--url', 'http://localhost:8088/'));
  await page.waitForFunction(
      () => !document.querySelector('#play').disabled, null, {timeout: 120000});
  const replay = fs.readFileSync(path.resolve(replayPath)).toString('base64');
  await page.evaluate(async data => {
    const r = await navigator.storage.getDirectory(),
          g = await r.getDirectoryHandle('wa'),
          p = await g.getDirectoryHandle('profile', {create: true}),
          h = await p.getFileHandle('benchmark.WAgame', {create: true}),
          w = await h.createWritable();
    await w.write(Uint8Array.from(atob(data), c => c.charCodeAt(0)));
    await w.close();
    let registry = {};
    try {
      const f = await (await p.getFileHandle('registry.json')).getFile();
      registry = JSON.parse(await f.text())
    } catch {
    }
    const sep = String.fromCharCode(92);
    for (const hive of ['HKEY_CURRENT_USER', 'HKEY_LOCAL_MACHINE']) {
      const parts =
          [hive, 'Software', 'Team17SoftwareLTD', 'WormsArmageddon', 'Options'];
      for (let i = 1; i <= parts.length; i++)
        registry[parts.slice(0, i).join(sep)] ??= {};
      Object.assign(registry[parts.join(sep)], {
        DisplayXSize: {type: 4, data: 1280},
        DisplayYSize: {type: 4, data: 960}
      })
    }
    const rw = await (await p.getFileHandle('registry.json', {
                 create: true
               })).createWritable();
    await rw.write(JSON.stringify(registry));
    await rw.close()
  }, replay);
  await page.bringToFront();
  fs.writeFileSync(
      path.join(out, 'system.json'),
      JSON.stringify(await cdp.send('SystemInfo.getInfo'), null, 2));
  const processInfo = await cdp.send('SystemInfo.getProcessInfo');
  const browserPid = processInfo.processInfo.find(p => p.type === 'browser').id;
  fs.writeFileSync(
      path.join(out, 'process-info.json'), JSON.stringify(processInfo));
  console.log(JSON.stringify(
      {ready: true, pid: process.pid, browserPid, out, diagnostic}));
  let tracing = false;
  const state = () =>
      page.evaluate(() => ({
                      log: window.recompLog?.slice(-12),
                      status: document.querySelector('#status')?.innerText,
                      size: [
                        document.querySelector('canvas').width,
                        document.querySelector('canvas').height
                      ],
                      relay: typeof window.recompNet,
                      heap: bench.heap,
                      gpuCalls: bench.gpuCalls
                    }));
  async function save() {
    const v = await page.evaluate(() => {
      const {created, ...m} = bench;
      const alive = created.filter(c => c.ref.deref());
      return {
        ...m, resources: {
          created: created.length,
          alive: alive.length,
          estimatedBytes: alive.reduce((s, c) => s + c.bytes, 0)
        },
            log: window.recompLog, now: performance.now()
      }
    });
    fs.writeFileSync(path.join(out, 'browser.json'), JSON.stringify(v));
    try {
      const csv = await page.evaluate(
          () => Module.FS.readFile('/tmp/benchframes.csv', {encoding: 'utf8'}));
      fs.writeFileSync(path.join(out, 'frames.csv'), csv)
    } catch (e) {
      event({type: 'csv-error', text: String(e)})
    }
  }
  const rl = readline.createInterface({input: process.stdin});
  for await (const l of rl) {
    const c = JSON.parse(l);
    let v;
    if (c.op === 'play') {
      await page.click('#play');
      v = 'launched'
    } else if (c.op === 'await-game') {
      const until = Date.now() + 60000;
      let visible = false;
      while (Date.now() < until) {
        const shot = (await page.screenshot()).toString('base64');
        visible = await page.evaluate(async b64 => {
          const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
          const bmp =
              await createImageBitmap(new Blob([bytes], {type: 'image/png'}));
          const image = new OffscreenCanvas(bmp.width, bmp.height),
                ctx = image.getContext('2d');
          ctx.drawImage(bmp, 0, 0);
          const pixels = ctx.getImageData(0, bmp.height - 80, 150, 70).data;
          let n = 0;
          for (let i = 0; i < pixels.length; i += 4)
            if (pixels[i] + pixels[i + 1] + pixels[i + 2] > 60) n++;
          bmp.close();
          return n / (pixels.length / 4) > .1
        }, shot);
        if (visible) break;
        await new Promise(r => setTimeout(r, 200));
      }
      if (!visible) throw Error('Replay HUD did not render; exclude this run');
      await new Promise(r => setTimeout(r, c.warmupMs ?? 2000));
      v = 'visible replay warmed';
    } else if (c.op === 'state')
      v = await state();
    else if (c.op === 'eval')
      v = await page.evaluate(c.expression);
    else if (c.op === 'shot') {
      v = path.join(out, c.name + '.png');
      await page.screenshot({path: v})
    } else if (c.op === 'wait') {
      await new Promise(r => setTimeout(r, c.ms));
      v = 'waited'
    } else if (c.op === 'trace-start') {
      await cdp.send('Tracing.start', {
        categories:
            'devtools.timeline,v8,gpu,renderer.scheduler,disabled-by-default-v8.cpu_profiler,disabled-by-default-v8.gc',
        transferMode: 'ReturnAsStream'
      });
      tracing = true
    } else if (c.op === 'trace-stop') {
      const done = new Promise(r => cdp.once('Tracing.tracingComplete', r));
      await cdp.send('Tracing.end');
      const {stream} = await done;
      const fd = fs.openSync(path.join(out, 'trace.json'), 'w');
      for (;;) {
        const x = await cdp.send('IO.read', {handle: stream});
        fs.writeSync(
            fd, x.base64Encoded ? Buffer.from(x.data, 'base64') : x.data);
        if (x.eof) break
      }
      fs.closeSync(fd);
      await cdp.send('IO.close', {handle: stream});
      tracing = false
    } else if (c.op === 'measure') {
      if (process.argv.includes('--x11'))
        require('node:child_process')
            .spawn(
                python,
                [
                  path.join(__dirname, 'profile_window_frames.py'), '--pgid',
                  String(browserPid), '--seconds', String(c.seconds || 30),
                  '--out', path.join(out, 'window-frames.json')
                ],
                {
                  stdio: [
                    'ignore',
                    fs.openSync(path.join(out, 'window-summary.json'), 'w'),
                    'inherit'
                  ]
                });
      await page.evaluate(() => {
        bench.measureStart =
        {
          time: performance.now(),
              gpuCalls: JSON.parse(JSON.stringify(bench.gpuCalls))
        }
      });
      await page.screenshot({path: path.join(out, 'start.png')});
      const start = await page.evaluate(() => {
        bench.raf = [];
        bench.canvas = [];
        bench.longTasks = [];
        return performance.now()
      });
      const child =
          require('node:child_process')
              .spawn(
                  python,
                  [
                    path.join(__dirname, 'profile_telemetry.py'), '--pgid',
                    String(browserPid), '--seconds', String(c.seconds || 30),
                    '--out', path.join(out, 'telemetry.jsonl')
                  ],
                  {
                    stdio: [
                      'ignore',
                      fs.openSync(
                          path.join(out, 'telemetry-summary.json'), 'w'),
                      'inherit'
                    ]
                  });
      await new Promise(r => setTimeout(r, (c.seconds || 30) * 1000));
      await page.screenshot({path: path.join(out, 'end.png')});
      await save();
      v = {start, end: await page.evaluate(() => performance.now())};
    } else if (c.op === 'save')
      await save();
    else if (c.op === 'close') {
      await save();
      rl.close();
      await context.close();
      break
    } else
      throw Error('unknown op');
    event({type: 'result', op: c.op, result: v});
    console.log(JSON.stringify({op: c.op, result: v}));
  }
})().catch(e => {
  console.error(e);
  process.exitCode = 1
});
