import * as mac from './src/tools/computer-use/macos.js';

async function demonstrate() {
  console.log('=== Image to Text Converter Demo ===\n');

  const outputPath = '/tmp/demo_screenshot.png';
  await mac.screenshot(outputPath);

  const metadata = mac.extractScreenshotMetadata(outputPath);
  console.log('📦 Metadata:', metadata);

  const analysis = await mac.analyzeScreen();
  
  console.log(`📊 Resolution: ${analysis.screen.resolution.width}x${analysis.screen.resolution.height}`);
  console.log(`📱 Scale factor: ${analysis.screen.scale}`);
  console.log(`🧩 Found ${analysis.elements.length} UI elements`);

  const llmContent = mac.formatScreenshotForLLM(metadata, analysis);
  console.log('\n🤖 LLM format (truncated):');
  console.log(llmContent.slice(0, 500) + '...');

  const grid = mac.renderTerminalGrid(analysis.elements, 80, 24);
  console.log('\n🖼️ Terminal grid:');
  console.log(grid);

  if (analysis.elements.length > 0) {
    console.log('\n📦 Element box:');
    console.log(mac.elementToBoxString(analysis.elements[0]));
  }

  import('node:fs').then(({ unlinkSync }) => {
    try { unlinkSync(outputPath); } catch {}
  });
}

demonstrate().catch(console.error);
