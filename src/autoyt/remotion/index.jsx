import React from 'react';
import {AbsoluteFill, Composition, interpolate, registerRoot, spring, useCurrentFrame, useVideoConfig} from 'remotion';

const ink = '#f5f7ff', muted = '#9aaac6', teal = '#50f2c6', purple = '#ad9cff';
const panel = {background: 'rgba(17,30,54,.88)', border: '1px solid #2a405c', borderRadius: 32};
const ease = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'};

function Network({frame, color = teal}) {
  return <svg viewBox="0 0 920 350" width="100%" height="100%" aria-label="Abstract concept diagram">
    {[0, 1, 2].map(row => [0, 1, 2].map(col => {
      const x = 150 + col*310, y = 65 + row*110;
      return <g key={`${row}-${col}`}>
        {col < 2 && [0, 1, 2].map(next => <line key={next} x1={x+24} y1={y} x2={x+286} y2={65+next*110}
          stroke={color} strokeWidth="2" opacity={.12 + .08*Math.sin(frame/18+row+next)}/>)}
        <circle cx={x} cy={y} r={20+3*Math.sin(frame/20+row+col)} fill="#132c3b" stroke={color} strokeWidth="3"/>
        <circle cx={x} cy={y} r="6" fill={color}/>
      </g>;
    }))}
  </svg>;
}

function Scene(props) {
  const frame = useCurrentFrame(), {fps, durationInFrames} = useVideoConfig();
  const enter = spring({frame, fps, config: {damping: 22, stiffness: 120, mass: .8}});
  const reveal = interpolate(frame, [0, 16], [0, 1], ease);
  const exit = interpolate(frame, [Math.max(1, durationInFrames-6), durationInFrames], [1, .1], ease);
  const first = props.template === 'hook', last = props.template === 'takeaway';
  const tag = first ? 'THE BIG IDEA' : last ? 'THE TAKEAWAY' : props.template === 'flow' ? 'CONCEPT VIEW' : 'IN FOCUS';
  const accent = props.template === 'focus' ? purple : teal;
  const headline = props.headline;
  const headingSize = headline.length > 65 ? 68 : headline.length > 40 ? 82 : 100;
  const excerpt = props.excerpt;
  return <AbsoluteFill style={{background: '#080f20', color: ink, fontFamily: 'Arial, "Nirmala UI", sans-serif'}}>
    <AbsoluteFill style={{background: `radial-gradient(ellipse at ${20+frame/25}% 20%, #163a43 0%, transparent 65%), radial-gradient(ellipse at 95% 65%, #242044 0%, transparent 55%)`}}/>
    <AbsoluteFill style={{opacity: .15, backgroundImage: 'linear-gradient(#587080 1px, transparent 1px),linear-gradient(90deg,#587080 1px,transparent 1px)', backgroundSize: '80px 80px', transform: `translateY(${frame*.18}px)`}}/>
    <div style={{position: 'absolute', top: 0, height: 6, background: accent, width: `${100*frame/Math.max(1,durationInFrames-1)}%`}}/>
    <div style={{width: 1080, height: 1920, transformOrigin: 'top left', transform: `scale(${props.width/1080})`, position: 'absolute'}}>
      <div style={{position: 'absolute', top: 110, left: 82, right: 120, display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: 22, letterSpacing: 4, color: muted}}>
        <span>AUTOYT <span style={{color: accent}}> / </span> TECH INSIGHTS</span><span>{String(props.index+1).padStart(2,'0')} / {String(props.total).padStart(2,'0')}</span>
      </div>
      <div style={{position: 'absolute', top: 270, left: 82, width: 864, opacity: reveal*exit, transform: `translateY(${(1-enter)*55}px)`}}>
        <div style={{display: 'inline-block', color: accent, border: `1px solid ${accent}66`, background: `${accent}12`, borderRadius: 100, padding: '15px 24px', fontSize: 22, fontWeight: 700, letterSpacing: 4}}>{tag}</div>
        <div style={{fontSize: headingSize, lineHeight: 1.06, fontWeight: 800, letterSpacing: -3, marginTop: 38, overflowWrap: 'anywhere'}}>{headline}</div>
        <div style={{height: 5, width: interpolate(frame,[5,28],[0,180],ease), background: accent, marginTop: 40}}/>
      </div>
      <div style={{position: 'absolute', top: 850, left: 82, width: 864, height: 450, opacity: reveal*exit, transform: `translateY(${(1-enter)*35}px)`}}>
        {props.template === 'flow' ? <div style={{...panel, height: 400, padding: 24}}><Network frame={frame} color={accent}/><div style={{color: muted, textAlign: 'center', fontSize: 20, letterSpacing: 3}}>ABSTRACT EXPLANATION · NOT A PRODUCT DEMO</div></div> :
          <div style={{...panel, padding: '42px 42px 46px', borderLeft: `5px solid ${accent}`}}>
            <div style={{fontSize: 19, letterSpacing: 4, color: accent, fontWeight: 700, marginBottom: 24}}>{last ? 'KEEP THIS IN MIND' : first ? 'WHY IT MATTERS' : 'THE KEY POINT'}</div>
            <div style={{fontSize: excerpt.length > 260 ? 28 : excerpt.length > 180 ? 32 : 37, lineHeight: 1.38, fontWeight: 500}}>{excerpt}</div>
          </div>}
      </div>
      <div style={{position: 'absolute', top: 1690, left: 82, color: muted, fontSize: 18, letterSpacing: 3}}>ORIGINAL MOTION GRAPHICS</div>
    </div>
  </AbsoluteFill>;
}

const defaults = {headline: 'Technology, explained clearly', excerpt: 'Original visuals follow your verified script.', template: 'hook', index: 0, total: 6, frames: 90, width: 1080, height: 1920, fps: 30};
const Root = () => <Composition id="TechScene" component={Scene} width={1080} height={1920} fps={30} durationInFrames={90}
  defaultProps={defaults} calculateMetadata={({props}) => ({width: props.width, height: props.height, fps: props.fps, durationInFrames: props.frames})}/>;
registerRoot(Root);
