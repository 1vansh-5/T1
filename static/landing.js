'use strict';
const reduceMotion=matchMedia('(prefers-reduced-motion: reduce)').matches;
const sculpture=document.getElementById('sculpture');
const robot=sculpture.querySelector('.robot');
if(!reduceMotion){
  sculpture.addEventListener('pointermove',event=>{
    if(event.pointerType==='touch')return;
    const box=sculpture.getBoundingClientRect();
    const x=(event.clientX-box.left)/box.width-.5;
    const y=(event.clientY-box.top)/box.height-.5;
    robot.style.transform=`translateX(-50%) rotateY(${x*14}deg) rotateX(${-y*6}deg)`;
  });
  sculpture.addEventListener('pointerleave',()=>robot.style.transform='translateX(-50%)');
}
if('IntersectionObserver' in window&&!reduceMotion){
  const observer=new IntersectionObserver(entries=>entries.forEach(e=>{if(e.isIntersecting){e.target.classList.add('visible');observer.unobserve(e.target);}}),{threshold:.08});
  document.querySelectorAll('.section-intro,.agent-card,.workflow-line,.principles-section,.closing').forEach(el=>{el.classList.add('reveal');observer.observe(el);});
}
