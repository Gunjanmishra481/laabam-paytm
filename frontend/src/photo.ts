// Bill photos are shrunk on the phone before upload: a 12 MP camera photo becomes a ~300 KB JPEG
// that still reads well, so the upload is quick on 4G.

const MAX_SIDE = 1800
const QUALITY = 0.82

export async function shrinkPhoto(file: File): Promise<Blob> {
  if (!file.type.startsWith('image/')) return file
  let bitmap: ImageBitmap
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' })
  } catch {
    return file // a format the browser can't decode (e.g. HEIC on some phones): send as is
  }
  const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height))
  if (scale === 1 && file.size < 1_500_000 && file.type === 'image/jpeg') {
    bitmap.close()
    return file
  }
  const canvas = document.createElement('canvas')
  canvas.width = Math.round(bitmap.width * scale)
  canvas.height = Math.round(bitmap.height * scale)
  const ctx = canvas.getContext('2d')
  if (!ctx) return file
  ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
  bitmap.close()
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', QUALITY))
  return blob ?? file
}
