"""Optional cross-vendor GPU comparison. Never opens or modifies original files."""
import numpy as np
from scipy.ndimage import gaussian_filter

PIXELS=96*96
# scipy gaussian_filter(sigma=1.5) uses radius 6 and half-sample reflect borders.
weights=np.exp(-np.arange(-6,7,dtype=float)**2/(2*1.5**2));weights/=weights.sum()
SHADER='''
@group(0) @binding(0) var<storage,read> images:array<f32>;
@group(0) @binding(1) var<storage,read> pairs:array<vec2<u32>>;
@group(0) @binding(2) var<storage,read_write> temp:array<f32>;
@group(0) @binding(3) var<storage,read_write> scores:array<f32>;
const weights:array<f32,13>=array<f32,13>(WEIGHTS);
fn reflect(v:i32)->u32 {if(v<0){return u32(-v-1);} if(v>=96){return u32(191-v);} return u32(v);}
@compute @workgroup_size(256)
fn horizontal(@builtin(global_invocation_id) id:vec3<u32>){
 let pixel=id.x;let pair=id.y;if(pixel>=9216u){return;}
 let a=pairs[pair].x*27648u;let b=pairs[pair].y*27648u;
 let x=i32(pixel%96u);let y=pixel/96u;var sum=0.0;
 for(var k=-6;k<=6;k++){let p=y*96u+reflect(x+k);sum+=weights[u32(k+6)]*images[a+p]*images[b+p];}
 temp[pair*9216u+pixel]=sum;
}
var<workgroup> partial:array<f32,256>;
@compute @workgroup_size(256)
fn finish(@builtin(local_invocation_id) local:vec3<u32>,@builtin(workgroup_id) group:vec3<u32>){
 let pair=group.x;let a=pairs[pair].x*27648u;let b=pairs[pair].y*27648u;var sum=0.0;
 for(var pixel=local.x;pixel<9216u;pixel+=256u){
  let x=pixel%96u;let y=i32(pixel/96u);var cross=0.0;
  for(var k=-6;k<=6;k++){cross+=weights[u32(k+6)]*temp[pair*9216u+reflect(y+k)*96u+x];}
  let ux=images[a+9216u+pixel];let uy=images[b+9216u+pixel];
  let vx=images[a+18432u+pixel];let vy=images[b+18432u+pixel];
  sum+=((2.0*ux*uy+0.0001)*(2.0*(cross-ux*uy)+0.0009))/((ux*ux+uy*uy+0.0001)*(vx+vy+0.0009));
 }
 partial[local.x]=sum;workgroupBarrier();
 for(var stride=128u;stride>0u;stride/=2u){if(local.x<stride){partial[local.x]+=partial[local.x+stride];}workgroupBarrier();}
 if(local.x==0u){scores[pair]=max(0.0,partial[0]/9216.0);}
}
'''.replace('WEIGHTS',','.join(f'{w:.12f}' for w in weights))

class GPUScorer:
    """One resident folder; bounded 256-pair scratch batches; explicit resource cleanup."""
    def __init__(self,photos,adapter=None):
        import wgpu
        self.wgpu=wgpu
        adapter=adapter or wgpu.gpu.request_adapter_sync(power_preference='high-performance')
        if adapter is None or adapter.info['adapter_type']=='CPU':raise RuntimeError('No hardware GPU available')
        self.info=dict(adapter.info);self.device=adapter.request_device_sync()
        self.photos=photos;self.indices={p.identity:i for i,p in enumerate(photos)};self.buffers=[]
        try:
            if len(photos)*PIXELS*3*4>self.device.limits['max-storage-buffer-binding-size']:
                raise RuntimeError('Folder exceeds GPU storage limit')
            stats=[]
            for p in photos:
                x=p.gray.astype(float)/255;mu=gaussian_filter(x,1.5)
                variance=np.maximum(0,gaussian_filter(x*x,1.5)-mu*mu)
                stats.append(np.stack((x,mu,variance)).astype(np.float32))
            data=np.ascontiguousarray(stats)
            if data.nbytes>self.device.limits['max-storage-buffer-binding-size']:raise RuntimeError('Folder exceeds GPU storage limit')
            flags=wgpu.BufferUsage
            self.images=self.device.create_buffer_with_data(data=data,usage=flags.STORAGE);self.buffers.append(self.images)
            self.pairs=self.device.create_buffer(size=256*8,usage=flags.STORAGE|flags.COPY_DST);self.buffers.append(self.pairs)
            self.temp=self.device.create_buffer(size=256*PIXELS*4,usage=flags.STORAGE);self.buffers.append(self.temp)
            self.output=self.device.create_buffer(size=256*4,usage=flags.STORAGE|flags.COPY_SRC);self.buffers.append(self.output)
            module=self.device.create_shader_module(code=SHADER)
            self.pipelines=[self.device.create_compute_pipeline(layout='auto',compute={'module':module,'entry_point':name}) for name in ('horizontal','finish')]
            self.bindings=[self.device.create_bind_group(layout=p.get_bind_group_layout(0),entries=[{'binding':i,'resource':{'buffer':buffer}} for i,buffer in enumerate(self.buffers) if n or i<3]) for n,p in enumerate(self.pipelines)]
        except Exception:
            self.close();raise
    def score_pairs(self,pairs,cancel=None):
        result=[]
        for start in range(0,len(pairs),256):
            if cancel and cancel.is_set():raise InterruptedError('Comparison cancelled')
            chunk=pairs[start:start+256]
            data=np.asarray([(self.indices[a.identity],self.indices[b.identity]) for a,b in chunk],dtype=np.uint32)
            self.device.queue.write_buffer(self.pairs,0,data)
            encoder=self.device.create_command_encoder()
            for i,pipeline in enumerate(self.pipelines):
                compute=encoder.begin_compute_pass();compute.set_pipeline(pipeline);compute.set_bind_group(0,self.bindings[i])
                compute.dispatch_workgroups(36,len(chunk),1) if i==0 else compute.dispatch_workgroups(len(chunk),1,1)
                compute.end()
            self.device.queue.submit([encoder.finish()])
            values=np.frombuffer(self.device.queue.read_buffer(self.output,size=len(chunk)*4),dtype=np.float32)
            result.extend(round(100*(.7*(1-(a.phash^b.phash).bit_count()/64)+.3*float(v)),2) for (a,b),v in zip(chunk,values))
        return result
    def close(self):
        for buffer in self.buffers:buffer.destroy()
        self.buffers=[]
        self.device.destroy()
