// Browser-side preparation. Original files remain unchanged on the owner's device.
export async function prepareGalleryImage(file,watermark=true){
 if(file.size>20*1024*1024)throw Error('Исходное фото должно быть не больше 20 МБ');
 if(!['image/jpeg','image/png','image/webp'].includes(file.type))throw Error('Выберите JPG, PNG или WebP');
 let image;try{image=await createImageBitmap(file)}catch{throw Error('Не удалось прочитать изображение. Сохраните его как JPG, PNG или WebP.')}
 try{
 if(image.width*image.height>50_000_000)throw Error('Слишком большое разрешение фото. Уменьшите его до 50 мегапикселей.');
 const scale=Math.min(1,1920/Math.max(image.width,image.height));const canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(image.width*scale));canvas.height=Math.max(1,Math.round(image.height*scale));const ctx=canvas.getContext('2d');if(!ctx)throw Error('Браузер не поддерживает обработку фото');ctx.fillStyle='#111';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(image,0,0,canvas.width,canvas.height);
 if(watermark){const size=Math.max(12,Math.round(canvas.width*.023));const pad=Math.max(12,Math.round(canvas.width*.025));ctx.font=`700 ${size}px Arial, sans-serif`;ctx.textAlign='right';ctx.textBaseline='bottom';ctx.shadowColor='rgba(0,0,0,.6)';ctx.shadowBlur=size*.25;ctx.fillStyle='rgba(255,255,255,.46)';ctx.fillText('UNDERHOOD',canvas.width-pad,canvas.height-pad);ctx.shadowBlur=0}
 const encode=q=>new Promise(resolve=>canvas.toBlob(resolve,'image/webp',q));let blob=await encode(.82);if(!blob)throw Error('Не удалось сжать фото');if(blob.size>1200*1024)blob=await encode(.68);if(!blob||blob.size>5*1024*1024)throw Error('Не удалось уменьшить фото до допустимого размера');
 return {file:new File([blob],file.name.replace(/\.[^.]+$/,'')+'.webp',{type:blob.type||'image/webp'}),width:canvas.width,height:canvas.height,originalSize:file.size,watermark};
 }finally{image.close()}
}
