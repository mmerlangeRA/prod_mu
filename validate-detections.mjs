import fs from 'node:fs';
import core from './detection-core.js';
const file=process.argv[2];
if(!file){console.error('Usage: node validate-detections.mjs <detections.json>');process.exit(1);}
try{
 const catalogue=JSON.parse(fs.readFileSync(new URL('./llm_description.json',import.meta.url),'utf8'));
 const models=catalogue.categories.flatMap(c=>c.images.map(m=>({...m,category_id:c.category_id})));
 const data=JSON.parse(fs.readFileSync(file,'utf8')),errors=core.validate(data,models);
 if(errors.length){console.error(errors.join('\n'));process.exitCode=1;}
 else console.log(`Valid: ${data.images.length} images, ${data.images.reduce((n,i)=>n+i.objects.length,0)} objects. Geometry and catalogue references checked; visual accuracy is not implied.`);
}catch(error){console.error(error.message);process.exitCode=1;}
