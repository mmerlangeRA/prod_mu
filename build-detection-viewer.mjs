import fs from 'node:fs';
import path from 'node:path';

const analysisDir = 'analysis';
const queryDir = 'queries';
const gpsFile = 'ncp_gps_frames.json';
const imageExtensions = new Set(['.png', '.jpg', '.jpeg', '.webp', '.gif']);
const framePattern = /^(.+)_f_(\d+)_/;

// Camera positions come only from ncp_gps_frames.json. Its `orientation` is the GPS bearing as a math angle
// (counter-clockwise from east); the compass heading of the panorama centre is 90 - orientation (see fensch.py).
function loadFrames() {
  const frames = new Map();
  if (!fs.existsSync(gpsFile)) return frames;
  for (const entry of JSON.parse(fs.readFileSync(gpsFile, 'utf8'))) {
    for (const image of entry.images) {
      frames.set(`${image.video_name}#${Number(image.frame)}`, {
        video_name: image.video_name,
        frame: Number(image.frame),
        latitude: entry.latitude,
        longitude: entry.longitude,
        heading_deg: ((90 - entry.orientation) % 360 + 360) % 360,
        timestamp_ms: entry.timestamp_ms
      });
    }
  }
  return frames;
}

function frameKey(name) {
  const match = framePattern.exec(path.basename(name));
  return match ? `${match[1]}#${Number(match[2])}` : null;
}

// Code prefix -> French category name; keep in sync with CATEGORY_BY_PREFIX in fensch.py.
const categoryByPrefix = {
  ABR: 'Abris bus', BAN: 'Bancs', BAR: 'Barrières', COR: 'Corbeille', ECL: 'Points d’éclairage', POT: 'Potelets',
  VEL: 'Supports vélos', RAL: 'Ralentisseurs', ARB: 'Arbres', BAC: 'Bacs à fleurs', 'BOU-E': 'Bouches d’égout',
  CEN: 'Cendriers', GRI: 'Grilles, avaloirs', HOR: 'Horodateurs', INC: 'Bornes à incendie', ORN: 'Ornements',
  PAPV: 'Points d’apport volontaire', PUB: 'Supports publicitaires', SAN: 'Sanitaire'
};

// The 44 Clavier codes with categories and classification, for the editor's code list (same rules as fensch.load_catalogue).
function loadCatalogue() {
  const clavier = JSON.parse(fs.readFileSync('Clavier Equipements CA_Val_de_Fensch.json', 'utf8'));
  const typePart = clavier.rubrics.flatMap(rubric => rubric.parts).find(part => part.name === 'Type');
  const english = new Map(fs.readFileSync('categories_to_collect_fr_en.csv', 'utf8').replace(/^\uFEFF/, '').trim().split(/\r?\n/).slice(1)
    .map(line => { const match = /^("([^"]*)"|[^,]*),(.*)$/.exec(line); return [match[2] ?? match[1], match[3].trim()]; }));
  const described = new Set(Object.keys(JSON.parse(fs.readFileSync('fensch_image_descriptions.json', 'utf8'))));
  return typePart.lexicons.map(({code, value}) => {
    const prefix = code in categoryByPrefix ? code : code.split('_')[0].replace(/\d+$/, '');
    const label = value.trim(), categoryFr = categoryByPrefix[prefix];
    if (!categoryFr || !english.has(categoryFr)) throw new Error(`No category for code ${code}`);
    const classification = code.startsWith('RAL') ? 'shape_subtype'
      : /classifier|déterminer/.test(label) ? 'category_fallback'
      : described.has(code) ? 'described_model' : 'direct_category';
    return {code, label, category_fr: categoryFr, category_en: english.get(categoryFr), classification};
  });
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

  const frames = loadFrames();
  const track = queryFiles.map(name => ({image_file: `${queryDir}/${name}`, ...(frames.get(frameKey(name)) || {})}))
    .filter(entry => Number.isFinite(entry.latitude))
    .sort((a, b) => a.video_name.localeCompare(b.video_name) || a.frame - b.frame);
  const datasets = [], batches = [];
  for (const analysisFile of analysisFiles) {
    const {features = [], ...data} = JSON.parse(fs.readFileSync(path.join(analysisDir, analysisFile), 'utf8'));
    if (!Array.isArray(data.images) || !data.images.length) throw new Error(`${analysisFile}: images must be a non-empty array.`);
    const batchIndex = batches.push({analysis_file: `${analysisDir}/${analysisFile}`, name: data.batch || path.parse(analysisFile).name,
      detector: data.detector || null, localization: data.localization || null, features}) - 1;
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
        batch_index: batchIndex,
        camera: frames.get(frameKey(queryName)) || null,
        data: {...data, images: [image]}
      });
    }
  }

  const template = fs.readFileSync('detection-viewer.template.html', 'utf8');
  const serialized = JSON.stringify({datasets, batches, track, catalogue: loadCatalogue()}).replace(/</g, '\\u003c');
  fs.writeFileSync('detection-viewer.html', template.replace('/*__DETECTION_DATA__*/', serialized));
  console.log(`Built detection-viewer.html with ${datasets.length} analysis/image pair(s), ${batches.reduce((n, b) => n + b.features.length, 0)} feature(s) and ${track.length} track frame(s).`);
  for (const dataset of datasets) {
    const count = dataset.data.images[0].objects.length;
    const method = dataset.pairing_method === 'json_image_file' ? 'JSON image_file' : 'analysis filename fallback';
    const location=dataset.camera?` GPS ${dataset.camera.latitude.toFixed(6)},${dataset.camera.longitude.toFixed(6)} heading ${dataset.camera.heading_deg.toFixed(1)}°`:' no GPS';
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
