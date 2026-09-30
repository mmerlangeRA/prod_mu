/* Shared offline detection contract and coordinate helpers. */
(function(root){
 'use strict';
 const categories=['banc','barriere','corbeille','potelet'];
 const decisions=['matched','category_fallback','ambiguous','insufficient_evidence','out_of_scope'];
 const confidence=['high','medium','low',null];
 function validate(data,models){
  const errors=[],bad=(path,message)=>errors.push(path+': '+message);
  const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
  const text=value=>typeof value==='string'&&value.trim().length>0;
  const strings=value=>Array.isArray(value)&&value.every(v=>typeof v==='string');
  if(!object(data))return ['Document must be an object.'];
  if(data.schema_version!=='1.0.0')bad('schema_version','expected 1.0.0');
  if(data.coordinate_system!=='normalized_xywh')bad('coordinate_system','expected normalized_xywh');
  if(!['manual_visual','model_inference','illustrative_example'].includes(data.analysis_kind))bad('analysis_kind','invalid value');
  if(data.notes!==undefined&&!strings(data.notes))bad('notes','expected an array of strings');
  if(!Array.isArray(data.images))return [...errors,'images: expected an array'];
  const imageIds=new Set(),files=new Set();
  data.images.forEach((im,i)=>{
   const p='images['+i+']';if(!object(im)){bad(p,'expected object');return;}
   if(!text(im.image_id)||imageIds.has(im.image_id))bad(p+'.image_id','required and must be unique');imageIds.add(im.image_id);
   const file=normalizePath(im.image_file);
   if(!text(im.image_file)||!file||files.has(file)||file.startsWith('/')||/^[a-z]+:/i.test(file)||file.split('/').some(s=>s==='..'))bad(p+'.image_file','use a unique relative file path without parent traversal');files.add(file);
   for(const k of ['width','height'])if(!Number.isSafeInteger(im[k])||im[k]<=0)bad(p+'.'+k,'expected positive integer');
   if(!Array.isArray(im.objects)){bad(p+'.objects','expected array');return;}
   const ids=new Set();
   im.objects.forEach((o,j)=>{
    const q=p+'.objects['+j+']';if(!object(o)){bad(q,'expected object');return;}
    if(!text(o.object_id)||ids.has(o.object_id))bad(q+'.object_id','required and must be unique within image');ids.add(o.object_id);
    if(![...categories,null].includes(o.category_id))bad(q+'.category_id','invalid category');
    if(!decisions.includes(o.decision))bad(q+'.decision','invalid decision');
    for(const k of ['category_confidence','model_confidence'])if(!confidence.includes(o[k]))bad(q+'.'+k,'expected high, medium, low or null');
    const resolved=['matched','category_fallback'].includes(o.decision);
    if(resolved){
     if(!categories.includes(o.category_id)||!text(o.model_name)||!Number.isInteger(o.model_id))bad(q,'resolved decision requires category and model name/ID');
     if(o.model_confidence===null)bad(q+'.model_confidence','required for resolved decision');
     if(models){const model=models.find(m=>m.name===o.model_name);if(!model||model.id!==o.model_id||model.category_id!==o.category_id)bad(q,'model name/ID/category do not match catalogue');else if((o.decision==='category_fallback')!==(model.entry_kind==='category_fallback'))bad(q,'decision does not match catalogue entry kind');}
    }else if(o.model_name!==null||o.model_id!==null||o.model_confidence!==null)bad(q,'unresolved decisions require null model fields and confidence');
    if(o.decision==='out_of_scope'&&o.category_id!==null)bad(q+'.category_id','must be null for out_of_scope');
    if(o.decision==='ambiguous'&&!categories.includes(o.category_id))bad(q+'.category_id','required for ambiguity within a category');
    if(o.category_id===null&&o.category_confidence!==null)bad(q+'.category_confidence','must be null without a category');
    if(o.category_id!==null&&o.category_confidence===null)bad(q+'.category_confidence','required when category is supplied');
    const b=o.bbox;
    if(!object(b)||!['x','y','width','height'].every(k=>typeof b[k]==='number'&&Number.isFinite(b[k])))bad(q+'.bbox','expected finite numeric x, y, width, height');
    else if(b.x<0||b.y<0||b.width<=0||b.height<=0||b.x+b.width>1+1e-9||b.y+b.height>1+1e-9)bad(q+'.bbox','must have positive size and stay within [0,1]');
    if(!['approximate','reviewed'].includes(o.bbox_quality))bad(q+'.bbox_quality','expected approximate or reviewed');
    for(const k of ['occluded','truncated'])if(typeof o[k]!=='boolean')bad(q+'.'+k,'expected boolean');
    if(!strings(o.observed_features))bad(q+'.observed_features','expected array of strings');
    if(!strings(o.alternative_candidates))bad(q+'.alternative_candidates','expected array of exact catalogue names');
    else {if(models)for(const name of o.alternative_candidates)if(!models.some(m=>m.name===name&&m.category_id===o.category_id&&m.entry_kind==='predefined_model'))bad(q+'.alternative_candidates','unknown or inappropriate candidate '+name);if(o.decision==='ambiguous'&&new Set(o.alternative_candidates).size<2)bad(q+'.alternative_candidates','ambiguity needs at least two candidates');}
    if(typeof o.notes!=='string')bad(q+'.notes','expected string');
    if(o.decision==='category_fallback'&&!o.observed_features?.length)bad(q+'.observed_features','describe visible differences supporting an unlisted model');
   });
  });
  return errors;
 }
 function normalizePath(path){return typeof path==='string'?path.replaceAll('\\','/').replace(/^\.\//,''):'';}
 function matchFile(path,files){
  const wanted=normalizePath(path),exact=files.filter(f=>normalizePath(f.webkitRelativePath||f.name)===wanted);
  if(exact.length===1)return {file:exact[0],error:null};
  if(exact.length>1)return {file:null,error:'Multiple files match '+wanted};
  const suffix=files.filter(f=>normalizePath(f.webkitRelativePath||f.name).endsWith('/'+wanted));
  if(suffix.length===1)return {file:suffix[0],error:null};
  const basename=wanted.split('/').pop(),matches=files.filter(f=>f.name===basename);
  return matches.length===1?{file:matches[0],error:null}:{file:null,error:matches.length?'Ambiguous filename: '+basename+'. Choose an image folder preserving paths.':'Missing image: '+wanted+'. Choose its image file or folder.'};
 }
 function pixels(box,width,height){return {x:box.x*width,y:box.y*height,width:box.width*width,height:box.height*height};}
 const api={validate,normalizePath,matchFile,pixels};root.DetectionData=api;
 if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(globalThis);
