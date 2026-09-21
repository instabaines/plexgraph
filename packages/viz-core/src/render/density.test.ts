import { expect,it } from "vitest";
import { aggregateDensity, aggregateGroups } from "./density";
it("preserves population and merges cell connectivity into one weighted link",()=>{
  const d=aggregateDensity(new Float32Array([0,0,0,0,1,1,1,1]),[{source:0,target:2},{source:1,target:3}],2);
  expect(d.points.map(p=>p.count)).toEqual([2,2]);
  expect(d.links).toEqual([[0,1,2]]);
});
it("bounds cells regardless of node population, including empty and coincident input",()=>{
  const positions=new Float32Array(200000);
  for(let i=0;i<positions.length;i++) positions[i]=(i*31%997)/997;
  const d=aggregateDensity(positions,[],32);
  expect(d.points.length).toBeLessThanOrEqual(1024);
  expect(d.points.reduce((sum,p)=>sum+p.count,0)).toBe(100000);
  expect(aggregateDensity(new Float32Array(),[]).points).toEqual([]);
  expect(aggregateDensity(new Float32Array(6),[]).points).toEqual([{x:0,y:0,count:3}]);
});
it("reports which cell every node belongs to so callers can aggregate attributes such as colour",()=>{
  const d=aggregateDensity(new Float32Array([0,0,0,0,1,1,1,1]),[],2);
  expect(Array.from(d.membership)).toEqual([0,0,1,1]);
});
it("counts how many connectors each aggregated link stands for",()=>{
  const d=aggregateDensity(new Float32Array([0,0,0,0,1,1,1,1]),[{source:0,target:2},{source:1,target:3},{source:0,target:3},{source:0,target:1}],2);
  expect(d.links).toEqual([[0,1,3]]);
});
it("aggregates by explicit groups, keeping group centroids and weighted links",()=>{
  const d=aggregateGroups(new Float32Array([0,0,2,0,10,10,12,10]),[{source:0,target:2},{source:1,target:3},{source:0,target:1}],[5,5,9,9]);
  expect(d.points).toEqual([{x:1,y:0,count:2},{x:11,y:10,count:2}]);
  expect(d.links).toEqual([[0,1,2]]);
  expect(Array.from(d.membership)).toEqual([0,0,1,1]);
});
