import React from 'react';
import {Icon} from '@iconify/react';
import lucide from '@iconify-json/lucide/icons.json';
import {Lottie} from '@remotion/lottie';
import {AbsoluteFill, Composition, Img, OffthreadVideo, interpolate, registerRoot, spring, useCurrentFrame, useVideoConfig} from 'remotion';

const clamp = {extrapolateLeft:'clamp',extrapolateRight:'clamp'};
const palettes = [
  {base:'#071629',light:'#147d91',accent:'#77ffe0',second:'#1e40af'},
  {base:'#20102f',light:'#8041aa',accent:'#e9b3ff',second:'#b33169'},
  {base:'#1d1a13',light:'#966126',accent:'#ffdb91',second:'#395b43'},
  {base:'#081d20',light:'#14695c',accent:'#a4ffe4',second:'#2478a0'},
];

function headlineSize(text) {
  const context=typeof document==='undefined'?null:document.createElement('canvas').getContext('2d');
  if(!context) return 64;
  for(let size=88;size>=40;size-=2) {
    context.font=`900 ${size}px Arial`;
    let lines=1,line='';
    for(const word of text.split(/\s+/)) {
      const candidate=line?`${line} ${word}`:word;
      if(line && context.measureText(candidate).width>864) {lines++;line=word;} else line=candidate;
      if(context.measureText(word).width>864) {lines+=Math.ceil(context.measureText(word).width/864)-1;line='';}
    }
    if(lines*size*1.08<=280) return size;
  }
  return 40;
}

function Environment({frame,index,palette}) {
  const variant=index%4;
  return <>
    <AbsoluteFill style={{background:`radial-gradient(ellipse at ${25+Math.sin(frame/90)*18}% ${35+index*9}%, ${palette.light} 0%, transparent 68%),linear-gradient(${125+index*35}deg,${palette.base},${palette.second})`}}/>
    <svg viewBox="0 0 1080 1920" width="100%" height="100%" style={{position:'absolute',inset:0}}>
      {variant===0 && <g transform={`translate(540 940) rotate(${frame*.08})`}>
        {[0,1,2,3,4].map(i=><ellipse key={i} cx="0" cy="0" rx={330+i*45} ry={105+i*25} transform={`rotate(${i*36})`} fill="none" stroke={palette.accent} strokeWidth="2" opacity={.18+i*.025}/>)}
        {Array.from({length:18},(_,i)=>{const a=i*Math.PI/9+frame/100;return <circle key={i} cx={Math.cos(a)*390} cy={Math.sin(a)*290} r={4+i%3*3} fill={palette.accent} opacity=".75"/>;})}
      </g>}
      {variant===1 && <g transform={`translate(540 940) rotate(${-12+frame*.035})`}>
        {Array.from({length:12},(_,i)=>{const depth=((i+frame/80)%12)/12,s=80+depth*1000;return <rect key={i} x={-s/2} y={-s*.66} width={s} height={s*1.32} rx={30} fill="none" stroke={palette.accent} strokeWidth={1+depth*3} opacity={depth*.45}/>;})}
      </g>}
      {variant===2 && <g transform={`translate(${-frame*.5} 0)`}>
        {Array.from({length:26},(_,i)=><g key={i} transform={`translate(${(i*191)%1400-150} ${(i*137+frame*(i%2?1:-.6))%1800})`} opacity={.10+(i%4)*.04}>
          <path d="M-35 -40 L-80 0 L-35 40 M35 -40 L80 0 L35 40" fill="none" stroke={palette.accent} strokeWidth="5"/>
        </g>)}
      </g>}
      {variant===3 && <g transform={`translate(540 940) rotate(${frame*.05})`}>
        {[0,1,2,3,4,5].map(i=><g key={i} transform={`rotate(${i*60})`}><path d="M0 -390 Q190 -180 0 0 Q-190 180 0 390" stroke={palette.accent} fill="none" strokeWidth="3" opacity=".25"/><circle cx="0" cy={Math.sin(frame/40+i)*360} r="9" fill={palette.accent}/></g>)}
      </g>}
      {Array.from({length:24},(_,i)=><circle key={i} cx={(i*173+frame*(i%2? .35:-.2)+1080)%1080} cy={(i*277+frame*.4)%1920} r={1+i%3} fill="white" opacity={.1+(i%4)*.035}/>)}
    </svg>
  </>;
}

function Scene(props) {
  const frame=useCurrentFrame(),{fps,durationInFrames}=useVideoConfig();
  const progress=frame/Math.max(1,durationInFrames-1),palette=palettes[props.index%palettes.length];
  const enter=spring({frame,fps,config:{damping:22,stiffness:110}});
  const reveal=interpolate(frame,[0,9],[0,1],clamp);
  const titleEnd=Math.min(durationInFrames-1,Math.round(fps*2.2));
  const titleOpacity=interpolate(frame,[0,8,Math.max(9,titleEnd-8),Math.max(10,titleEnd)],[0,1,1,0],clamp);
  const size=React.useMemo(()=>headlineSize(props.headline),[props.headline]);
  const photo=props.background?.src;
  const icon=props.icon && lucide.icons[props.icon.name];
  return <AbsoluteFill style={{background:palette.base,color:'white',fontFamily:'Arial, "Nirmala UI", sans-serif',overflow:'hidden'}}>
    <AbsoluteFill style={{transform:`scale(${1.04+progress*.13}) translate(${(props.index%2?1:-1)*progress*20}px,${-progress*18}px)`}}>
      {photo?(props.background.kind==='video'?<OffthreadVideo src={photo} muted style={{width:'100%',height:'100%',objectFit:'cover'}}/>:<Img src={photo} style={{width:'100%',height:'100%',objectFit:'cover'}}/>):<Environment frame={frame} index={props.index} palette={palette}/>}
    </AbsoluteFill>
    <AbsoluteFill style={{background:photo?'linear-gradient(180deg,rgba(0,0,0,.65) 0%,rgba(0,0,0,.08) 42%,rgba(0,0,0,.2) 60%,rgba(0,0,0,.66) 100%)':'radial-gradient(ellipse at 50% 48%,transparent 22%,rgba(0,0,0,.38) 100%)'}}/>
    <div style={{position:'absolute',width:1080,height:1920,transform:`scale(${props.width/1080})`,transformOrigin:'top left'}}>
      {!photo && <div style={{position:'absolute',left:290,top:670,width:500,height:500,display:'flex',alignItems:'center',justifyContent:'center',opacity:reveal,transform:`translateY(${Math.sin(frame/30)*16}px) rotate(${Math.sin(frame/70)*4}deg) scale(${.86+enter*.14+progress*.06})`}}>
        {icon && <Icon icon={{...icon,width:24,height:24}} width={300} height={300} style={{color:palette.accent,filter:`drop-shadow(0 0 35px ${palette.light})`}}/>}
      </div>}
      {photo && icon && <div style={{position:'absolute',left:82,top:610,color:palette.accent,opacity:titleOpacity*.7,transform:`translateY(${Math.sin(frame/25)*7}px)`}}><Icon icon={{...icon,width:24,height:24}} width={72} height={72}/></div>}
      {props.animation_data && <div style={{position:'absolute',left:photo?830:80,top:photo?590:610,width:photo?130:920,height:photo?130:920,opacity:photo ? titleOpacity*.45 : .3,transform:`rotate(${frame*.06}deg)`}}><Lottie animationData={props.animation_data} loop style={{width:'100%',height:'100%'}}/></div>}
      <div style={{position:'absolute',top:245,left:82,width:864,opacity:titleOpacity,transform:`translateX(${(1-enter)*(props.index%2?70:-70)}px)`,textShadow:'0 4px 28px rgba(0,0,0,.7)'}}>
        <div style={{fontSize:size,fontWeight:900,lineHeight:1.25,letterSpacing:-1.5,overflowWrap:'anywhere'}}><span style={{background:'#3d50ff',padding:'6px 14px',boxDecorationBreak:'clone',WebkitBoxDecorationBreak:'clone',textShadow:'0 2px 3px rgba(0,0,0,.25)'}}>{props.headline}</span></div>
      </div>
    </div>
  </AbsoluteFill>;
}

const defaults={headline:'Technology, in motion',excerpt:'',template:'hook',index:0,total:4,frames:90,width:1080,height:1920,fps:30};
const Root=()=> <Composition id="TechScene" component={Scene} width={1080} height={1920} fps={30} durationInFrames={90} defaultProps={defaults} calculateMetadata={({props})=>({width:props.width,height:props.height,fps:props.fps,durationInFrames:props.frames})}/>;
registerRoot(Root);
