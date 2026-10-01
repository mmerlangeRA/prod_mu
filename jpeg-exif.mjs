import fs from 'node:fs';

const typeSizes = {1:1,2:1,3:2,4:4,5:8,7:1,9:4,10:8};

function parseExif(segment) {
  if (segment.length < 14 || segment.subarray(0,6).toString('binary') !== 'Exif\0\0') return {};
  const tiff = 6, little = segment.toString('ascii',tiff,tiff+2) === 'II';
  const inBounds = (offset,length) => offset >= 0 && offset + length <= segment.length;
  const u16 = offset => {
    const absolute=tiff+offset;if(!inBounds(absolute,2))throw new Error('Invalid EXIF offset');
    return little?segment.readUInt16LE(absolute):segment.readUInt16BE(absolute);
  };
  const u32 = offset => {
    const absolute=tiff+offset;if(!inBounds(absolute,4))throw new Error('Invalid EXIF offset');
    return little?segment.readUInt32LE(absolute):segment.readUInt32BE(absolute);
  };
  if (u16(2) !== 42) return {};
  function fieldValue(entry) {
    const type=u16(entry+2),count=u32(entry+4),size=(typeSizes[type]||1)*count,relative=size<=4?entry+8:u32(entry+8),start=tiff+relative;
    if(!inBounds(start,size))throw new Error('Invalid EXIF value offset');
    if(type===2)return segment.subarray(start,start+count).toString('ascii').replace(/\0+$/,'');
    const values=[];
    for(let index=0;index<count;index++){
      if(type===1||type===7)values.push(segment[start+index]);
      else if(type===3)values.push(little?segment.readUInt16LE(start+2*index):segment.readUInt16BE(start+2*index));
      else if(type===4)values.push(little?segment.readUInt32LE(start+4*index):segment.readUInt32BE(start+4*index));
      else if(type===5){const numerator=little?segment.readUInt32LE(start+8*index):segment.readUInt32BE(start+8*index),denominator=little?segment.readUInt32LE(start+8*index+4):segment.readUInt32BE(start+8*index+4);values.push(denominator?numerator/denominator:null);}
      else if(type===9)values.push(little?segment.readInt32LE(start+4*index):segment.readInt32BE(start+4*index));
      else if(type===10){const numerator=little?segment.readInt32LE(start+8*index):segment.readInt32BE(start+8*index),denominator=little?segment.readInt32LE(start+8*index+4):segment.readInt32BE(start+8*index+4);values.push(denominator?numerator/denominator:null);}
    }
    return values.length===1?values[0]:values;
  }
  function readIfd(offset) {
    const fields={},count=u16(offset);
    if(count>512)throw new Error('Unreasonable EXIF field count');
    for(let index=0;index<count;index++){
      const entry=offset+2+index*12,tag=u16(entry);
      try{fields[tag]=fieldValue(entry);}catch{}
    }
    return fields;
  }
  const root=readIfd(u32(4)),gps=Number.isInteger(root[0x8825])?readIfd(root[0x8825]):{},exif=Number.isInteger(root[0x8769])?readIfd(root[0x8769]):{};
  const coordinate=(values,ref) => Array.isArray(values)&&values.length===3&&values.every(Number.isFinite)
    ? (values[0]+values[1]/60+values[2]/3600)*(['S','W'].includes(ref)?-1:1) : null;
  const latitude=coordinate(gps[0x0002],gps[0x0001]),longitude=coordinate(gps[0x0004],gps[0x0003]);
  const imageDirection=Number.isFinite(gps[0x0011])?gps[0x0011]:null,destinationBearing=Number.isFinite(gps[0x0018])?gps[0x0018]:null;
  const heading=destinationBearing??imageDirection;
  return {
    latitude,longitude,
    orientation:Number.isInteger(root[0x0112])?root[0x0112]:1,
    image_direction_deg:imageDirection,
    image_direction_ref:typeof gps[0x0010]==='string'?gps[0x0010]:null,
    destination_bearing_deg:destinationBearing,
    destination_bearing_ref:typeof gps[0x0017]==='string'?gps[0x0017]:null,
    heading_deg:heading,
    heading_source:destinationBearing!==null?'GPSDestBearing':imageDirection!==null?'GPSImgDirection':null,
    captured_at:typeof exif[0x9003]==='string'?exif[0x9003]:null
  };
}

export function readJpegMetadata(filePath) {
  const buffer=fs.readFileSync(filePath);let offset=2,width=null,height=null,exif={},projection_type=null;
  if(buffer[0]!==0xff||buffer[1]!==0xd8)throw new Error(`${filePath}: not a JPEG file.`);
  while(offset+4<=buffer.length&&buffer[offset]===0xff){
    const marker=buffer[offset+1];if(marker===0xda||marker===0xd9)break;
    const length=buffer.readUInt16BE(offset+2);if(length<2||offset+2+length>buffer.length)break;
    const segment=buffer.subarray(offset+4,offset+2+length);
    if(marker===0xe1&&segment.subarray(0,6).toString('binary')==='Exif\0\0')exif=parseExif(segment);
    if(marker===0xe1&&segment.subarray(0,29).toString('ascii').startsWith('http://ns.adobe.com/xap/1.0/')){
      const match=segment.toString('utf8').match(/<GPano:ProjectionType>([^<]+)<\/GPano:ProjectionType>/);if(match)projection_type=match[1];
    }
    if([0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf].includes(marker)&&segment.length>=5){height=segment.readUInt16BE(1);width=segment.readUInt16BE(3);}
    offset+=2+length;
  }
  return {width,height,projection_type,...exif};
}
