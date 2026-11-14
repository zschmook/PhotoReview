"""Generate a short MJPEG AVI for local player tests; no downloads or originals."""
from io import BytesIO
from pathlib import Path
import struct
from PIL import Image,ImageDraw


def make_video(path,seconds=4,fps=10):
    width,height=640,360;frames=[]
    for number in range(seconds*fps):
        image=Image.new('RGB',(width,height),'#193d5c');draw=ImageDraw.Draw(image)
        draw.rectangle((0,210,width,height),fill='#478b9f')
        draw.polygon([(0,210),(145,65),(290,210)],fill='#96ad9c')
        draw.polygon([(210,210),(390,80),(570,210)],fill='#a5bdac')
        x=70+number*10;draw.ellipse((x,250,x+46,296),fill='#f1c66b')
        draw.text((24,24),'VIDEO PREVIEW / GENERATED TEST CLIP',fill='white')
        draw.text((24,325),f'Frame {number+1} / {seconds*fps}',fill='white')
        buffer=BytesIO();image.save(buffer,'JPEG',quality=85);frames.append(buffer.getvalue())
    def chunk(tag,data):return tag+struct.pack('<I',len(data))+data+(b'\0' if len(data)%2 else b'')
    def list_chunk(tag,data):return chunk(b'LIST',tag+data)
    largest=max(map(len,frames))
    avih=struct.pack('<14I',1_000_000//fps,largest*fps,0,0x10,len(frames),0,1,largest,width,height,0,0,0,0)
    strh=struct.pack('<4s4sIHHIIIIIIIIhhhh',b'vids',b'MJPG',0,0,0,0,1,fps,0,len(frames),largest,0xffffffff,0,0,0,width,height)
    strf=struct.pack('<IiiHH4sIiiII',40,width,height,1,24,b'MJPG',width*height*3,0,0,0,0)
    header=list_chunk(b'hdrl',chunk(b'avih',avih)+list_chunk(b'strl',chunk(b'strh',strh)+chunk(b'strf',strf)))
    data=[];index=[];offset=4
    for frame in frames:
        part=chunk(b'00dc',frame);data.append(part);index.append(struct.pack('<4sIII',b'00dc',0x10,offset,len(frame)));offset+=len(part)
    payload=b'AVI '+header+list_chunk(b'movi',b''.join(data))+chunk(b'idx1',b''.join(index))
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'RIFF'+struct.pack('<I',len(payload))+payload)
    return path
