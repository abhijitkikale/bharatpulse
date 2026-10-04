
/* ---------- risk monitor: MOVE + HY spread vs Nifty ---------- */
const mean=a=>a.reduce((x,y)=>x+y,0)/a.length, sdev=a=>{const m=mean(a);return Math.sqrt(mean(a.map(v=>(v-m)**2)))};
const corr=(x,y)=>{const mx=mean(x),my=mean(y);let n=0,dx=0,dy=0;for(let i=0;i<x.length;i++){n+=(x[i]-mx)*(y[i]-my);dx+=(x[i]-mx)**2;dy+=(y[i]-my)**2}return n/Math.sqrt(dx*dy)};
const pctRank=(a,v)=>100*a.filter(x=>x<=v).length/a.length;
function renderRisk(){
  const c=D.credit;
  if(!c||c.d.length<60){$('#risk-body').innerHTML='<div class="card c12">MOVE / HY spread data is unavailable in this build.</div>';return}
  const {d:dates,nifty:nf,move:mo,hy}=c, last=a=>a.at(-1), chg=(a,n)=>a.at(-1)-a.at(-1-n);
  const dd=100*(last(nf)/Math.max(...nf)-1);
  const stale=(new Date()-new Date(c.hy_asof))/864e5>6;
  $('#rk-sub').innerHTML=`${dates[0]} to ${dates.at(-1)} · ${dates.length} Nifty trading days · HY spread as of ${c.hy_asof}${stale?' <span class="warn">(⚠ not refreshed recently)</span>':''} · MOVE as of ${c.move_asof}`;
  const tile=(k,v,s,cl='')=>`<div class="tile"><div class="k">${k}</div><div class="v ${cl}">${v}</div><div class="k">${s}</div></div>`;
  $('#rk-tiles').innerHTML=tile('Nifty 50',num(last(nf),0),`${dd.toFixed(1)}% from period high`,dd<0?'dn':'up')+
    tile('MOVE',last(mo).toFixed(1),`${pctRank(mo,last(mo)).toFixed(0)}th percentile · 1M chg ${chg(mo,21).toFixed(1)}`)+
    tile('HY spread',last(hy).toFixed(2)+'%',`${pctRank(hy,last(hy)).toFixed(0)}th percentile · 1M chg ${chg(hy,21).toFixed(2)}`,last(hy)>3?'dn':'')+
    tile('HY spread, 1W change',(chg(hy,5)>=0?'+':'')+chg(hy,5).toFixed(2)+' pts',chg(hy,5)>0?'credit stress rising':'credit stress easing',chg(hy,5)>0?'dn':'up');
  const line=(x,y,name,col,extra={})=>Object.assign({type:'scatter',mode:'lines',x,y,name,line:{color:col,width:1.6}},extra);
  Plotly.react($('#rk-a'),[line(dates,nf,'Nifty 50','#4cc9f0',{yaxis:'y'}),line(dates,mo,'MOVE','#f5a623',{yaxis:'y2'}),line(dates,hy,'HY spread %','#ef5b5b',{yaxis:'y3'})],
    layout({hovermode:'x unified',margin:{b:36,l:60,t:10},xaxis:{tickangle:0,type:'date',anchor:'y3'},
      yaxis:{domain:[.7,1],ticksuffix:'',title:{text:'Nifty 50'}},yaxis2:{domain:[.36,.64],ticksuffix:'',gridcolor:'#263040',title:{text:'MOVE'}},yaxis3:{domain:[0,.3],ticksuffix:'',gridcolor:'#263040',title:{text:'HY OAS %'}}}),CFG);
  const z=a=>{const m=mean(a),s=sdev(a);return a.map(v=>(v-m)/s)};
  Plotly.react($('#rk-b'),[line(dates,z(nf),'Nifty 50','#4cc9f0'),line(dates,z(mo),'MOVE','#f5a623',{line:{color:'#f5a623',width:1.3}}),line(dates,z(hy),'HY spread','#ef5b5b',{line:{color:'#ef5b5b',width:1.3}})],
    layout({showlegend:true,legend:{orientation:'h',y:1.1},hovermode:'x unified',xaxis:{tickangle:0,type:'date'},yaxis:{ticksuffix:'',title:{text:'z-score'}},margin:{b:36,t:10}}),CFG);
  const ret=nf.slice(1).map((v,i)=>v/nf[i]-1),dM=mo.slice(1).map((v,i)=>v-mo[i]),dH=hy.slice(1).map((v,i)=>v-hy[i]);
  const roll=(x,y,w)=>x.map((_,i)=>i+1<w?null:corr(x.slice(i+1-w,i+1),y.slice(i+1-w,i+1)));
  const drawC=w=>Plotly.react($('#rk-c'),[line(dates.slice(1),roll(ret,dM,w),'Nifty vs ΔMOVE','#f5a623'),line(dates.slice(1),roll(ret,dH,w),'Nifty vs ΔHY spread','#ef5b5b')],
    layout({showlegend:true,legend:{orientation:'h',y:1.12},hovermode:'x unified',xaxis:{tickangle:0,type:'date'},yaxis:{ticksuffix:'',range:[-1,1],title:{text:'correlation'}},margin:{b:36,t:10}}),CFG);
  drawC(+($('#rk-win .on')?.dataset.w||63));
  $('#rk-win').onclick=e=>{const w=e.target.dataset.w;if(!w)return;[...$('#rk-win').children].forEach(b=>b.classList.toggle('on',b===e.target));drawC(+w)};
  $('#rk-corr').textContent=`Full period: Nifty daily % vs ΔMOVE = ${corr(ret,dM).toFixed(2)}, vs ΔHY spread = ${corr(ret,dH).toFixed(2)}`;
  const jumps=hy.map((v,i)=>i<5?null:[i,v-hy[i-5]]).filter(Boolean).sort((a,b)=>b[1]-a[1]);const picked=[];
  for(const j of jumps){if(picked.every(p=>Math.abs(p[0]-j[0])>15))picked.push(j);if(picked.length===5)break}
  picked.sort((a,b)=>a[0]-b[0]);const pc=(a,i)=>100*(a[i]/a[i-5]-1);
  $('#rk-tbl').innerHTML='<thead><tr><th>Window ending</th><th>HY spread (pts)</th><th>HY level</th><th>MOVE change</th><th>Nifty 50 change</th></tr></thead><tbody>'+
    picked.map(([i,j])=>`<tr><td>${dates[i]}</td><td class="dn">+${j.toFixed(2)}</td><td>${hy[i].toFixed(2)}%</td><td>${(mo[i]-mo[i-5]>=0?'+':'')+(mo[i]-mo[i-5]).toFixed(1)}</td><td class="${cls(pc(nf,i))}">${fmt(pc(nf,i))}</td></tr>`).join('')+'</tbody>';
}
