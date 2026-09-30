import fs from 'node:fs';
const files = fs.readdirSync('.').filter(name => name.endsWith('.json'));
const sources = files.map(name => ({name, data: JSON.parse(fs.readFileSync(name, 'utf8'))})).filter(source => Array.isArray(source.data.rubrics));
const template = fs.readFileSync('viewer.template.html', 'utf8');
fs.writeFileSync('index.html', template.replace('/*__CATALOGUE_DATA__*/', () => JSON.stringify(sources).replace(/</g, '\\u003c')));
console.log(`Built index.html from ${sources.length} catalogue JSON file(s).`);
