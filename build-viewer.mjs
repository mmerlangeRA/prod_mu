import fs from 'node:fs';
const template = fs.readFileSync('viewer.template.html', 'utf8');
fs.writeFileSync('index.html', template);
console.log('Built index.html without embedded catalogue data. Select JSON files in the page to load a catalogue.');
