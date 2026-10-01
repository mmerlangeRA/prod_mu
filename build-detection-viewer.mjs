import fs from 'node:fs';
import path from 'node:path';
import {readJpegMetadata} from './jpeg-exif.mjs';

const analysisDir = 'analysis';
const queryDir = 'queries';
const imageExtensions = new Set(['.png', '.jpg', '.jpeg', '.webp', '.gif']);

function readImageMetadata(filePath) {
  return ['.jpg', '.jpeg'].includes(path.extname(filePath).toLowerCase())
    ? readJpegMetadata(filePath)
    : {};
}

function validateObjects(analysisFile, imageIndex, objects) {
  if (!Array.isArray(objects)) throw new Error(`${analysisFile}: images[${imageIndex}].objects must be an array.`);
  for (const [objectIndex, object] of objects.entries()) {
    const box = object.bbox;
    if (!box || !['x', 'y', 'width', 'height'].every(key => Number.isFinite(box[key]))) {
      throw new Error(`${analysisFile}: images[${imageIndex}].objects[${objectIndex}] has an invalid bbox.`);
    }
    if (box.x < 0 || box.y < 0 || box.width <= 0 || box.height <= 0 || box.x + box.width > 1 || box.y + box.height > 1) {
      throw new Error(`${analysisFile}: images[${imageIndex}].objects[${objectIndex}] bbox is outside normalized image bounds.`);
    }
  }
}

function build() {
  const analysisFiles = fs.existsSync(analysisDir)
    ? fs.readdirSync(analysisDir).filter(name => path.extname(name).toLowerCase() === '.json').sort()
    : [];
  const queryFiles = fs.existsSync(queryDir)
    ? fs.readdirSync(queryDir).filter(name => imageExtensions.has(path.extname(name).toLowerCase())).sort()
    : [];
  const queryByName = new Map(queryFiles.map(name => [name, name]));
  const queryByStem = new Map();
  for (const name of queryFiles) {
    const stem = path.parse(name).name;
    const matches = queryByStem.get(stem) || [];
    matches.push(name);
    queryByStem.set(stem, matches);
  }

  const datasets = [];
  for (const analysisFile of analysisFiles) {
    const data = JSON.parse(fs.readFileSync(path.join(analysisDir, analysisFile), 'utf8'));
    if (!Array.isArray(data.images) || !data.images.length) throw new Error(`${analysisFile}: images must be a non-empty array.`);
    for (const [imageIndex, image] of data.images.entries()) {
      validateObjects(analysisFile, imageIndex, image.objects);
      const internalName = typeof image.image_file === 'string' ? path.basename(image.image_file) : '';
      let queryName = queryByName.get(internalName), pairingMethod = 'json_image_file';
      if (!queryName && data.images.length === 1) {
        const matches = queryByStem.get(path.parse(analysisFile).name) || [];
        if (matches.length === 1) {
          queryName = matches[0];
          pairingMethod = 'analysis_filename';
        }
      }
      if (!queryName) {
        throw new Error(`${analysisFile}: images[${imageIndex}] references ${JSON.stringify(image.image_file)}, but no exact matching file exists in queries/.`);
      }
      datasets.push({
        analysis_file: `${analysisDir}/${analysisFile}`,
        analysis_image_index: imageIndex,
        display_name: data.images.length === 1 ? analysisFile : `${analysisFile} · ${queryName}`,
        image_file: `${queryDir}/${queryName}`,
        internal_image_file: image.image_file,
        internal_name_matches: pairingMethod === 'json_image_file',
        pairing_method: pairingMethod,
        exif: readImageMetadata(path.join(queryDir, queryName)),
        data: {...data, images: [image]}
      });
    }
  }

  const template = fs.readFileSync('detection-viewer.template.html', 'utf8');
  const serialized = JSON.stringify(datasets).replace(/</g, '\\u003c');
  fs.writeFileSync('detection-viewer.html', template.replace('/*__DETECTION_DATA__*/', serialized));
  console.log(`Built detection-viewer.html with ${datasets.length} analysis/image pair(s).`);
  for (const dataset of datasets) {
    const count = dataset.data.images[0].objects.length;
    const method = dataset.pairing_method === 'json_image_file' ? 'JSON image_file' : 'analysis filename fallback';
    const location=Number.isFinite(dataset.exif.latitude)&&Number.isFinite(dataset.exif.longitude)?` GPS ${dataset.exif.latitude.toFixed(6)},${dataset.exif.longitude.toFixed(6)} heading ${dataset.exif.heading_deg?.toFixed(1)??'n/a'}°`:' no GPS';
    console.log(`- ${dataset.analysis_file}[${dataset.analysis_image_index}] ↔ ${dataset.image_file}: ${count} objects (${method};${location})`);
  }
}

function runBuild() {
  try { build(); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}

runBuild();
if (process.argv.includes('--watch')) {
  let timer;
  const changed = () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      process.exitCode = 0;
      runBuild();
    }, 150);
  };
  for (const directory of [analysisDir, queryDir]) fs.watch(directory, changed);
  console.log('Watching analysis/ and queries/. Refresh detection-viewer.html after files change.');
}
