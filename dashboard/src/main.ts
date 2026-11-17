import {Component, signal, computed} from '@angular/core';
import {bootstrapApplication} from '@angular/platform-browser';
import {CommonModule} from '@angular/common';
import {FormsModule} from '@angular/forms';
import {DataChart} from './chart';

type Station={id:number,name:string,powiat:string,hub:boolean,matched:boolean,passMin:number,passMax:number};
type Catalog={meta:any,stations:Station[],carriers:{id:number,name:string,category:string}[]};
type Offer={station:number,day:string,days:number,departures:number|null,average:number|null};
type Transfer={hub:number,day:string,arrivalCarrier:number,departureCarrier:number,period:string,seconds:number,count:number};
type Utk={station:number,carrier:number,month:number,stops:number};
type Routes={gaps:any[],journeys:any[],destinations:any[]};
const TEAL='#087e8b', GOLD='#c68635', NAVY='#253d56', MUTED='#718096';
const EMPTY:Routes={gaps:[],journeys:[],destinations:[]};
const MONTHS=['Sty','Lut','Mar','Kwi','Maj','Cze','Lip','Sie','Wrz','Paź','Lis','Gru'];

@Component({selector:'app-root',standalone:true,imports:[CommonModule,FormsModule,DataChart],templateUrl:'./app.html'})
export class App {
  readonly Math=Math;
  catalog=signal<Catalog|null>(null); offers=signal<Offer[]>([]); transfers=signal<Transfer[]>([]); utk=signal<Utk[]>([]);
  error=signal(''); tab=signal('offer'); loaded=signal(false); routeLoading=signal(false); routeError=signal('');
  county=signal(''); station=signal(0); hubsOnly=signal(true);
  origin=signal(0); destination=signal(0); day=signal('all'); routeCarrier=signal(0); routePeriod=signal('all'); routes=signal<Routes>(EMPTY);
  hub=signal(0); transferDay=signal('all'); transferPeriod=signal('all'); arriving=signal(0); departing=signal(0);
  utkStation=signal(0); utkCarrier=signal(0); quarter=signal(0);
  private routeCache=new Map<number,Routes>(); private routeRequest=0;
  periods=['Szczyt poranny','Międzyszczyt','Szczyt popołudniowy','Wieczór','Noc'];
  tabs=[{id:'offer',number:'01',name:'Oferta i dostępność',desc:'Z1–Z5'},{id:'transfer',number:'02',name:'Przesiadki',desc:'Z6–Z7'},{id:'market',number:'03',name:'Popyt i przewoźnicy',desc:'Z8–Z10'},{id:'method',number:'04',name:'Dane i metodologia',desc:'Źródła i definicje'}];
  questions=[
    ['Z1','Średnia liczba odjazdów w dni robocze i weekendy','SUM(odjazdy) / liczba dni danego typu. Dni bez kursów pozostają w mianowniku.'],
    ['Z2','Najdłuższa przerwa na bezpośredniej relacji','Różnica kolejnych różnych godzin odjazdu A→B, osobno w każdym dniu, włącznie 06:00–22:00. Wszystkie firmy łącznie. Bez przerw przed pierwszym i po ostatnim kursie. Mniej niż dwa odjazdy: brak miary, nie zero.'],
    ['Z3','Zmiana oferty weekendowej','100 × (średnia robocza − średnia weekendowa) / średnia robocza. Wartość ujemna oznacza wzrost. Przy zerowej bazie wskaźnik jest nieokreślony.'],
    ['Z4','Czas podróży według przewoźnika i pory dnia','Przyjazd do B − odjazd z A, bez postoju na stacji docelowej. Średnia ważona liczbą kursów; min i max z rzeczywistych zdarzeń.'],
    ['Z5','Liczba bezpośrednich stacji docelowych','Unikalne dalsze stacje w jednym kursie. Obejmuje także stacje pośrednie. Oba końce w regionalnym zbiorze 325 stacji. Dla całego okresu liczymy sumę zbiorów, nie sumę liczników.'],
    ['Z6','Średnia i mediana oczekiwania','Potencjalne pary różnych kursów z dozwoloną wysiadką i wsiadaniem w tym samym węźle i dniu kursowania. Rozpatrujemy wyłącznie oczekiwania 5–30 minut. Nie jest to analiza wszystkich możliwych przesiadek.'],
    ['Z7','Przesiadki 10–25 minut przy zmianie przewoźnika','Pary różnych przewoźników z oczekiwaniem 600–1500 sekund / wszystkie ich pary z oczekiwaniem 300–1800 sekund. Granice włącznie. Brak par oznacza brak wskaźnika.'],
    ['Z8','Odjazdy na 1000 pasażerów','1000 × średnia dzienna odjazdów / środek przedziału dobowej wymiany UTK. Wskaźnik orientacyjny: rozkład 2026 zestawiono ze statystyką 2025; nie jest to obłożenie ani porównanie plan–wykonanie.'],
    ['Z9','Miesięczne zatrzymania w 2025 roku','Suma liczby zatrzymań UTK w wybranych stacjach, miesiącach i przewoźnikach. Brak wiersza dla stacji to brak danych; pustą komórkę miesięczną w istniejącym źródle ETL interpretuje jako zero.'],
    ['Z10','Udział dominującego przewoźnika w węźle','Zatrzymania przewoźnika / wszystkie zatrzymania węzła w wybranym kwartale lub roku 2025. Mianownik zawsze obejmuje wszystkich przewoźników.']
  ];
  constructor(){this.load();}
  async json<T>(path:string):Promise<T>{const r=await fetch('data/'+path);if(!r.ok)throw new Error('Nie udało się pobrać '+path);return r.json();}
  async load(){this.error.set('');try{
    const [catalog,offer,transfers,utk]=await Promise.all([this.json<Catalog>('catalog.json'),this.json<Offer[]>('offer.json'),this.json<Transfer[]>('transfers.json'),this.json<Utk[]>('utk.json')]);
    this.catalog.set(catalog);this.offers.set(offer);this.transfers.set(transfers);this.utk.set(utk);this.loaded.set(true);
    this.setOrigin(catalog.stations.find(s=>s.name==='Wrocław Główny')!.id);
  }catch(e){this.error.set(String(e));}}
  stations=computed(()=>this.catalog()?.stations??[]);
  periodLabel=computed(()=>[this.catalog()?.meta.start,this.catalog()?.meta.end].map(x=>x?String(x).split('-').reverse().join('.'):'—').join(' — '));
  dayCount(kind:string){return this.offers().find(x=>x.day===kind)?.days??0;}
  matchedCount=computed(()=>this.stations().filter(s=>s.matched).length);
  utkStationCount=computed(()=>new Set(this.utk().map(x=>x.station)).size);
  originMatched=computed(()=>this.stations().find(s=>s.id===this.origin())?.matched??false);
  setTab(id:string){this.tab.set(id);window.scrollTo({top:0,behavior:'instant'});}
  hubs=computed(()=>this.stations().filter(s=>s.hub));
  counties=computed(()=>[...new Set(this.stations().map(s=>s.powiat))].sort((a,b)=>a.localeCompare(b,'pl')));
  carriers=computed(()=>this.catalog()?.carriers.filter(c=>this.utk().some(u=>u.carrier===c.id)||this.transfers().some(t=>t.arrivalCarrier===c.id||t.departureCarrier===c.id))??[]);
  filteredStations=computed(()=>this.stations().filter(s=>(!this.county()||s.powiat===this.county())&&(!this.station()||s.id===this.station())&&(!this.hubsOnly()||s.hub)));
  name(id:number){return this.stations().find(s=>s.id===id)?.name??String(id);}
  carrierName(id:number){return this.catalog()?.carriers.find(c=>c.id===id)?.name??String(id);}
  n(value:number|null|undefined,digits=0){return value==null||!Number.isFinite(value)?'—':new Intl.NumberFormat('pl-PL',{maximumFractionDigits:digits,minimumFractionDigits:digits}).format(value);}
  avg(id:number,day:string){return this.offers().find(o=>o.station===id&&o.day===day)?.average??null;}
  decline(id:number){const a=this.avg(id,'weekday'),b=this.avg(id,'weekend');return a!=null&&a>0&&b!=null?100*(a-b)/a:null;}
  offerRows=computed(()=>this.filteredStations().map(s=>({...s,weekday:this.avg(s.id,'weekday'),weekend:this.avg(s.id,'weekend'),decline:this.decline(s.id)})).sort((a,b)=>(b.weekday??-1)-(a.weekday??-1)));
  offerTotals=computed(()=>{
    const r=this.offerRows().filter(s=>s.matched), weekday=r.reduce((s,x)=>s+(x.weekday??0),0),weekend=r.reduce((s,x)=>s+(x.weekend??0),0);
    return {weekday:r.length?weekday:null,weekend:r.length?weekend:null,decline:weekday?100*(weekday-weekend)/weekday:null,matched:r.length,count:this.offerRows().length};
  });
  selectStation(name:string){const s=this.stations().find(x=>x.name===name);if(s){this.station.set(s.id);this.setOrigin(s.id);}}
  resetOffer(){this.station.set(0);this.county.set('');this.hubsOnly.set(true);}
  setStation(id:number){this.station.set(+id);if(+id){this.hubsOnly.set(false);this.county.set('');this.setOrigin(+id);}}
  baseChart(){return {color:[TEAL,GOLD,NAVY,'#9cae70','#9678a5','#ba6974'],tooltip:{trigger:'axis',backgroundColor:'#fff',borderColor:'#e3e7ea',textStyle:{color:NAVY}},legend:{bottom:0,icon:'circle',itemWidth:9,itemHeight:9,textStyle:{color:MUTED}},grid:{left:48,right:18,top:20,bottom:78},yAxis:{type:'value',splitLine:{lineStyle:{color:'#edf0f2'}},axisLabel:{color:MUTED}},xAxis:{type:'category',axisLine:{lineStyle:{color:'#e0e5e8'}},axisTick:{show:false},axisLabel:{color:MUTED,fontSize:11,interval:0,rotate:18}}};}
  offerChart=computed(()=>{const r=this.offerRows().filter(s=>s.matched).slice(0,10),b=this.baseChart();return {...b,xAxis:{...b.xAxis,data:r.map(s=>s.name)},series:[{name:'Dzień roboczy',type:'bar',barMaxWidth:28,data:r.map(s=>s.weekday),itemStyle:{borderRadius:[3,3,0,0]}},{name:'Weekend',type:'bar',barMaxWidth:28,data:r.map(s=>s.weekend),itemStyle:{borderRadius:[3,3,0,0]}}]};});
  declineChart=computed(()=>{const r=this.offerRows().filter(s=>s.decline!=null).sort((a,b)=>b.decline!-a.decline!).slice(0,8);return {...this.baseChart(),legend:{show:false},grid:{left:145,right:48,top:12,bottom:32},xAxis:{type:'value',axisLabel:{formatter:'{value}%',color:MUTED},splitLine:{lineStyle:{color:'#edf0f2'}}},yAxis:{type:'category',inverse:true,data:r.map(s=>s.name),axisTick:{show:false},axisLine:{show:false},axisLabel:{fontSize:11,color:NAVY,width:132,overflow:'truncate'}},series:[{type:'bar',barMaxWidth:19,data:r.map(s=>({value:+s.decline!.toFixed(2),itemStyle:{color:s.decline!>0?GOLD:TEAL,borderRadius:[0,3,3,0]}})),label:{show:true,position:'right',fontSize:11,formatter:(p:any)=>this.n(p.value,1)+'%'}}]};});
  async setOrigin(id:number){id=+id;this.origin.set(id);this.destination.set(0);this.routeCarrier.set(0);this.routePeriod.set('all');this.routeError.set('');this.routes.set(EMPTY);const request=++this.routeRequest;this.routeLoading.set(true);try{const data=this.routeCache.get(id)??await this.json<Routes>('routes/'+id+'.json');this.routeCache.set(id,data);if(request===this.routeRequest)this.routes.set(data);}catch(e){if(request===this.routeRequest)this.routeError.set(String(e));}finally{if(request===this.routeRequest)this.routeLoading.set(false);}}
  routeDestinations=computed(()=>{const ids=new Set(this.routes().destinations.filter(r=>this.day()==='all'||r.day===this.day()).map(r=>r.destination));return this.stations().filter(s=>ids.has(s.id));});
  routeGaps=computed(()=>{const map=new Map<number,any>();for(const x of this.routes().gaps.filter(r=>(this.day()==='all'||r.day===this.day())&&(!this.destination()||r.destination===this.destination()))){const p=map.get(x.destination)??{destination:x.destination,max:null,sum:0,intervals:0,serviceDays:0};if(x.max!=null)p.max=Math.max(p.max??0,x.max);p.sum+=(x.average??0)*x.intervals;p.intervals+=x.intervals;p.serviceDays+=x.serviceDays;map.set(x.destination,p);}return [...map.values()].map(r=>({...r,average:r.intervals?r.sum/r.intervals:null})).sort((a,b)=>(b.max??-1)-(a.max??-1));});
  maxGap=computed(()=>this.routeGaps().find(r=>r.max!=null)?.max??null);
  journeyRows=computed(()=>{
    const map=new Map<string,any>();for(const x of this.routes().journeys.filter(r=>(this.day()==='all'||r.day===this.day())&&(!this.destination()||r.destination===this.destination())&&(!this.routeCarrier()||r.carrier===this.routeCarrier())&&(this.routePeriod()==='all'||r.period===this.routePeriod()))){const key=x.destination+'-'+x.carrier,p=map.get(key)??{destination:x.destination,carrier:x.carrier,min:Infinity,max:0,sum:0,count:0};p.min=Math.min(p.min,x.min);p.max=Math.max(p.max,x.max);p.sum+=x.average*x.count;p.count+=x.count;map.set(key,p);}return [...map.values()].map(p=>({...p,average:p.sum/p.count})).sort((a,b)=>a.average-b.average);
  });
  gapChart=computed(()=>{const r=this.routeGaps().filter(x=>x.max!=null).slice(0,8);return {...this.baseChart(),legend:{show:false},grid:{left:155,right:45,top:16,bottom:25},xAxis:{type:'value',axisLabel:{formatter:'{value} min'},splitLine:{lineStyle:{color:'#edf0f2'}}},yAxis:{type:'category',inverse:true,data:r.map(x=>this.name(x.destination)),axisLabel:{color:NAVY,fontSize:11,width:140,overflow:'truncate'},axisLine:{show:false},axisTick:{show:false}},series:[{type:'bar',data:r.map(x=>x.max),barMaxWidth:20,itemStyle:{color:NAVY,borderRadius:[0,3,3,0]},label:{show:true,position:'right',fontSize:11,formatter:(p:any)=>this.n(p.value)},markLine:{silent:true,symbol:'none',lineStyle:{color:GOLD,type:'dashed'},label:{formatter:'60 min'},data:[{xAxis:60}]}}]};});
  journeyChart=computed(()=>{const r=this.journeyRows().slice(0,8),b=this.baseChart();return {...b,grid:{left:50,right:20,top:20,bottom:110},xAxis:{...b.xAxis,data:r.map(x=>this.name(x.destination)+'\n'+this.carrierName(x.carrier)),axisLabel:{...b.xAxis.axisLabel,rotate:22,width:120,overflow:'truncate'}},series:[{name:'Minimum',type:'bar',data:r.map(x=>x.min)},{name:'Średnia',type:'bar',data:r.map(x=>x.average)},{name:'Maksimum',type:'bar',data:r.map(x=>x.max)}]};});
  transferRows=computed(()=>this.transfers().filter(r=>(!this.hub()||r.hub===this.hub())&&(this.transferDay()==='all'||r.day===this.transferDay())&&(this.transferPeriod()==='all'||r.period===this.transferPeriod())&&(!this.arriving()||r.arrivalCarrier===this.arriving())&&(!this.departing()||r.departureCarrier===this.departing())));
  transferStats(rows:Transfer[]){const count=rows.reduce((s,r)=>s+r.count,0),sum=rows.reduce((s,r)=>s+r.seconds*r.count,0);const sorted=[...rows].sort((a,b)=>a.seconds-b.seconds);const at=(index:number)=>{let n=0;for(const r of sorted){n+=r.count;if(n>index)return r.seconds/60;}return 0;};const changed=rows.filter(r=>r.arrivalCarrier!==r.departureCarrier),changeCount=changed.reduce((s,r)=>s+r.count,0),good=changed.filter(r=>r.seconds>=600&&r.seconds<=1500).reduce((s,r)=>s+r.count,0);return {count,average:count?sum/count/60:null,median:count?(at(Math.floor((count-1)/2))+at(Math.floor(count/2)))/2:null,changeCount,good,percent:changeCount?good/changeCount*100:null};}
  transferTotal=computed(()=>this.transferStats(this.transferRows()));
  hubStats=computed(()=>this.hubs().filter(s=>!this.hub()||s.id===this.hub()).map(s=>({...s,...this.transferStats(this.transferRows().filter(r=>r.hub===s.id))})));
  transferChart=computed(()=>{const r=this.hubStats(),b=this.baseChart();return {...b,xAxis:{...b.xAxis,data:r.map(s=>s.name)},series:[{name:'Średnia, min',type:'bar',data:r.map(s=>s.average),barMaxWidth:25},{name:'Mediana, min',type:'bar',data:r.map(s=>s.median),barMaxWidth:25}]};});
  qualityChart=computed(()=>{const r=this.hubStats(),b=this.baseChart();return {...b,yAxis:{...b.yAxis,max:100,axisLabel:{formatter:'{value}%'}},xAxis:{...b.xAxis,data:r.map(s=>s.name)},series:[{name:'10–25 min',type:'bar',stack:'share',data:r.map(s=>s.percent),barMaxWidth:45},{name:'Poza oknem',type:'bar',stack:'share',data:r.map(s=>s.percent==null?null:100-s.percent),barMaxWidth:45,itemStyle:{color:'#e8edef'}}]};});
  histogram=computed(()=>{const data=Array(26).fill(0);for(const r of this.transferRows())data[Math.floor(r.seconds/60)-5]+=r.count;return {...this.baseChart(),legend:{show:false},grid:{left:55,right:20,top:15,bottom:30},xAxis:{type:'category',data:data.map((_,i)=>String(i+5)),axisLabel:{color:MUTED}},series:[{type:'bar',data:data.map((v,i)=>({value:v,itemStyle:{color:TEAL}}))}]};});
  utkFiltered=computed(()=>this.utk().filter(r=>(!this.utkStation()||r.station===this.utkStation())&&(!this.utkCarrier()||r.carrier===this.utkCarrier())&&(!this.quarter()||Math.ceil(r.month/3)===this.quarter())));
  utkSum=computed(()=>this.utkFiltered().reduce((s,r)=>s+r.stops,0));
  monthly=computed(()=>MONTHS.map((name,i)=>({name,month:i+1,total:this.utkFiltered().some(r=>r.month===i+1)?this.utkFiltered().filter(r=>r.month===i+1).reduce((s,r)=>s+r.stops,0):null})).filter(r=>!this.quarter()||Math.ceil(r.month/3)===this.quarter()));
  monthlyChart=computed(()=>{const r=this.monthly(),b=this.baseChart();return {...b,legend:{show:false},grid:{left:65,right:25,top:20,bottom:35},xAxis:{...b.xAxis,data:r.map(x=>x.name),axisLabel:{color:MUTED}},series:[{name:'Zatrzymania',type:'line',smooth:false,symbolSize:7,data:r.map(x=>x.total),lineStyle:{width:3},areaStyle:{color:'#087e8b12'}}]};});
  marketRows=computed(()=>this.stations().filter(s=>!this.utkStation()||s.id===this.utkStation()).map(s=>{const o=this.offers().filter(x=>x.station===s.id),days=o.reduce((n,x)=>n+x.days,0),average=s.matched?o.reduce((n,x)=>n+(x.departures??0),0)/days:null,passengers=(s.passMin+s.passMax)/2;return {...s,average,passengers,intensity:average!=null&&passengers>0?1000*average/passengers:null};}));
  intensityChart=computed(()=>{const r=this.marketRows().filter(s=>s.intensity!=null&&(this.utkStation()||s.hub)).sort((a,b)=>a.intensity!-b.intensity!);return {...this.baseChart(),legend:{show:false},grid:{left:145,right:45,top:20,bottom:30},xAxis:{type:'value',axisLabel:{color:MUTED},splitLine:{lineStyle:{color:'#edf0f2'}}},yAxis:{type:'category',inverse:true,data:r.map(s=>s.name),axisLabel:{fontSize:11,color:NAVY,width:132,overflow:'truncate'},axisLine:{show:false},axisTick:{show:false}},series:[{type:'bar',barMaxWidth:22,data:r.map(s=>+s.intensity!.toFixed(2)),itemStyle:{color:TEAL,borderRadius:[0,3,3,0]},label:{show:true,position:'right',fontSize:10,formatter:(p:any)=>this.n(p.value,2)}}]};});
  scatterChart=computed(()=>({color:[TEAL],tooltip:{trigger:'item',formatter:(p:any)=>`${p.data.name}<br>Wymiana: ${this.n(p.value[0])} os./dobę (szacunek)<br>Odjazdy: ${this.n(p.value[1],1)}/dobę`},grid:{left:65,right:25,top:20,bottom:55},xAxis:{type:'value',name:'Szacowana wymiana / dobę',nameLocation:'middle',nameGap:32,splitLine:{lineStyle:{color:'#edf0f2'}},axisLabel:{color:MUTED}},yAxis:{type:'value',name:'Odjazdy / dobę',splitLine:{lineStyle:{color:'#edf0f2'}},axisLabel:{color:MUTED}},series:[{type:'scatter',data:this.marketRows().filter(s=>s.average!=null).map(s=>({name:s.name,value:[s.passengers,s.average],symbolSize:s.hub?14:8})),itemStyle:{opacity:.75}}]}));
  shares=computed(()=>this.hubs().filter(s=>!this.utkStation()||s.id===this.utkStation()).map(s=>{const r=this.utk().filter(x=>x.station===s.id&&(!this.quarter()||Math.ceil(x.month/3)===this.quarter())),total=r.reduce((a,b)=>a+b.stops,0),parts=this.carriers().map(c=>({carrier:c.id,name:c.name,count:r.filter(x=>x.carrier===c.id).reduce((a,b)=>a+b.stops,0)})).filter(p=>p.count>0).sort((a,b)=>b.count-a.count);return {...s,total,parts,leader:parts.at(0),percent:total?100*parts[0].count/total:null};}));
  shareChart=computed(()=>{const r=this.shares(),b=this.baseChart();return {...b,grid:{left:50,right:20,top:20,bottom:110},yAxis:{...b.yAxis,max:100,axisLabel:{formatter:'{value}%'}},xAxis:{...b.xAxis,data:r.map(s=>s.name)},series:this.carriers().filter(c=>r.some(s=>s.parts.some(p=>p.carrier===c.id))).map(c=>({name:c.name,type:'bar',stack:'share',barMaxWidth:45,data:r.map(s=>s.total?100*(s.parts.find(p=>p.carrier===c.id)?.count??0)/s.total:null)}))};});
  downloadCsv(){let rows:any[]=[];if(this.tab()==='offer')rows=this.offerRows().map(r=>({Stacja:r.name,Powiat:r.powiat,Robocze:r.weekday,Weekend:r.weekend,Spadek_proc:r.decline}));else if(this.tab()==='transfer')rows=this.hubStats().map(r=>({Wezel:r.name,Pary:r.count,Srednia_min:r.average,Mediana_min:r.median,Zmiana_przewoznika:r.changeCount,Okno_proc:r.percent}));else rows=this.monthly().map(r=>({Miesiac:r.month,Zatrzymania:r.total}));this.csv(rows,'kolej-'+this.tab()+'.csv');}
  downloadRoutes(){this.csv(this.journeyRows().map(r=>({Od:this.name(this.origin()),Do:this.name(r.destination),Przewoznik:this.carrierName(r.carrier),Minimum:r.min,Srednia:r.average,Maksimum:r.max,Kursy:r.count})),'kolej-czasy-podrozy.csv');}
  csv(rows:any[],name:string){if(!rows.length)return;const keys=Object.keys(rows[0]),quote=(x:any)=>'"'+String(x??'').replaceAll('"','""')+'"',body='\ufeff'+[keys,...rows.map(r=>keys.map(k=>r[k]))].map(r=>r.map(quote).join(';')).join('\r\n');const url=URL.createObjectURL(new Blob([body],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  print(){window.print();}
}
bootstrapApplication(App).catch(console.error);
