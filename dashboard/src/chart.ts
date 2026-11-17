import {Component, input, output, effect, viewChild, ElementRef, OnDestroy} from '@angular/core';
import * as echarts from 'echarts/core';
import {BarChart, LineChart, ScatterChart} from 'echarts/charts';
import {GridComponent, TooltipComponent, LegendComponent, MarkLineComponent, AriaComponent} from 'echarts/components';
import {SVGRenderer} from 'echarts/renderers';
echarts.use([BarChart,LineChart,ScatterChart,GridComponent,TooltipComponent,LegendComponent,MarkLineComponent,AriaComponent,SVGRenderer]);

@Component({selector:'data-chart',standalone:true,template:`<div #surface class="chart-surface" role="img" [attr.aria-label]="label()"></div>`})
export class DataChart implements OnDestroy {
  options = input<any>(); label=input('Wykres danych'); selected=output<string>();
  surface=viewChild<ElementRef<HTMLDivElement>>('surface');
  private chart?:echarts.ECharts; private resize?:ResizeObserver;
  constructor(){effect(()=>{
    const el=this.surface()?.nativeElement, option=this.options();
    if(!el || !option) return;
    if(!this.chart){
      this.chart=echarts.init(el,undefined,{renderer:'svg'});
      this.resize=new ResizeObserver(()=>this.chart?.resize()); this.resize.observe(el);
      this.chart.on('click',(p:any)=>this.selected.emit(p.name));
    }
    this.chart.setOption({animationDuration:300,textStyle:{fontFamily:'Segoe UI, sans-serif'},aria:{enabled:true},...option},true);
  });}
  ngOnDestroy(){this.resize?.disconnect();this.chart?.dispose();}
}
