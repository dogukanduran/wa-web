// A usable adapter must present to this browser's canvas, not just allocate an
// offscreen texture. Hybrid Vulkan systems can expose a discrete adapter whose
// device cannot import the compositor's external-memory canvas image.
export async function createCanvasDevice(canvas, gpu = navigator.gpu) {
  let lastError;
  for (const powerPreference of ["high-performance", "low-power"]) {
    let device;
    let context;
    let timeout;
    let accepted = false;
    try {
      const adapter = await gpu.requestAdapter({ powerPreference });
      if (!adapter) throw new Error("WebGPU gave no adapter.");
      const wanted = ["texture-compression-bc", "depth32float-stencil8", "float32-filterable",
                      "unorm16-texture-formats", "rg11b10ufloat-renderable"];
      const requiredFeatures = wanted.filter(f => adapter.features.has(f));
      const requiredLimits = {};
      for (const k of ["maxTextureDimension2D", "maxBufferSize", "maxStorageBufferBindingSize",
                       "maxUniformBufferBindingSize", "maxBindGroups", "maxSampledTexturesPerShaderStage",
                       "maxSamplersPerShaderStage", "maxStorageBuffersPerShaderStage",
                       "maxUniformBuffersPerShaderStage", "maxDynamicUniformBuffersPerPipelineLayout",
                       "maxVertexBuffers", "maxVertexAttributes", "maxColorAttachments",
                       "maxInterStageShaderVariables"])
        if (k in adapter.limits) requiredLimits[k] = adapter.limits[k];
      device = await adapter.requestDevice({ requiredFeatures, requiredLimits });
      context = canvas.getContext("webgpu");
      if (!context) throw new Error("WebGPU canvas is unavailable.");
      const lost = device.lost.then(info => {
        throw new Error(info.message || "WebGPU device lost during canvas presentation.");
      });
      const present = async () => {
        context.configure({ device, format: gpu.getPreferredCanvasFormat(), alphaMode: "opaque" });
        const encoder = device.createCommandEncoder();
        encoder.beginRenderPass({ colorAttachments: [{
          view: context.getCurrentTexture().createView(),
          clearValue: { r: 0, g: 0, b: 0, a: 1 }, loadOp: "clear", storeOp: "store",
        }] }).end();
        device.queue.submit([encoder.finish()]);
        await device.queue.onSubmittedWorkDone();
        // Queue completion can precede Chromium's deferred SharedImage import
        // error. Let presentation and the device-lost notification run too.
        await new Promise(requestAnimationFrame);
        await new Promise(requestAnimationFrame);
      };
      await Promise.race([present(), lost, new Promise((_, reject) => {
        timeout = setTimeout(() => reject(new Error("WebGPU canvas presentation timed out.")), 3000);
      })]);
      console.info("WebGPU canvas verified:", powerPreference, adapter.info?.vendor, adapter.info?.architecture);
      accepted = true;
      return device;
    } catch (error) {
      lastError = error;
      console.warn("WebGPU adapter could not present:", powerPreference, error.message);
    } finally {
      clearTimeout(timeout);
      context?.unconfigure();
      // The successful device is handed to WASM; failed devices retain no
      // canvas or application resources when another adapter is attempted.
      if (device && !accepted) device.destroy();
    }
  }
  throw lastError || new Error("No WebGPU adapter can present to the canvas.");
}
