import { it, expect } from "vitest";
import { GraphIndex } from "./graph-index";
import type { WireConnector } from "../ir/types";
it("finds keys and indexes hyperedge neighborhoods without expanding pairs", () => {
  const nodes=Array.from({length:100},(_,id)=>({id,key:`Person ${id}`,attrs:{role:"research"}}));
  const connectors:WireConnector[]=[{id:7,endpoints:[0,1,2,3],layer_id:null,t_start:null,t_end:null,directed:false,weight:null,attrs:{}},
    {id:8,endpoints:[0,99],layer_id:null,t_start:null,t_end:null,directed:true,weight:null,attrs:{}}];
  const index=new GraphIndex(nodes,connectors);
  expect(index.search("PERSON 99").map(n=>n.id)).toEqual([99]);
  expect(index.search("")).toEqual([]);
  expect(index.neighborhood(0).nodes).toEqual(new Set([0,1,2,3,99]));
  expect(index.neighborhood(0).connectors).toEqual(new Set([7,8]));
  expect(index.neighborhood(98).nodes).toEqual(new Set([98]));
  expect(index.neighborhood(100).nodes.size).toBe(0);
});

const edge=(id:number,endpoints:number[]):WireConnector=>({id,endpoints,layer_id:null,t_start:null,t_end:null,directed:false,weight:null,attrs:{}});
const people=[
  {id:0,key:"a",attrs:{team:"red",age:31}},{id:1,key:"b",attrs:{team:"red",age:22}},{id:2,key:"c",attrs:{team:"blue",age:45}},
  {id:3,key:"d",attrs:{team:"blue",age:29}},{id:4,key:"e",attrs:{team:"green",age:38}},{id:5,key:"lonely",attrs:{}},
];
// a-b, hyperedge {b,c,d}, d-e; node 5 is isolated
const graph=()=>new GraphIndex(people,[edge(1,[0,1]),edge(2,[1,2,3]),edge(3,[3,4])]);

it("counts distinct neighbours, treating a hyperedge as one link to every other member",()=>{
  const g=graph();
  expect([0,1,2,3,4,5].map(n=>g.degree(n))).toEqual([1,3,2,3,1,0]);
});
it("summarises attributes: categorical values by frequency, numeric ranges, and skips missing values",()=>{
  const s=Object.fromEntries(graph().attributeSummary().map(a=>[a.name,a]));
  expect(s.team.kind).toBe("categorical");
  expect(s.team.values).toEqual([{value:"blue",count:2},{value:"red",count:2},{value:"green",count:1}]);
  expect(s.age.min).toBe(22);expect(s.age.max).toBe(45);
  const many=new GraphIndex(Array.from({length:50},(_,id)=>({id,key:String(id),attrs:{score:id*1.5,tag:`t${id}`}})),[]);
  const m=Object.fromEntries(many.attributeSummary().map(a=>[a.name,a]));
  expect(m.score).toMatchObject({kind:"numeric",min:0,max:73.5});
  expect(m.tag.kind).toBe("categorical");
});
it("marks attributes with too many distinct values as text instead of listing them",()=>{
  const g=new GraphIndex(Array.from({length:300},(_,id)=>({id,key:String(id),attrs:{name:`n${id}`}})),[]);
  expect(g.attributeSummary()[0]).toMatchObject({name:"name",kind:"text",distinct:300});
});
it("filters nodes by attribute value, numeric range and degree, combining criteria with AND",()=>{
  const g=graph();
  expect([...g.matchNodes({attribute:"team",values:["red","green"]})]).toEqual([0,1,4]);
  expect([...g.matchNodes({attribute:"age",range:[29,40]})]).toEqual([0,3,4]);
  expect([...g.matchNodes({minDegree:2})]).toEqual([1,2,3]);
  expect([...g.matchNodes({attribute:"team",values:["blue"],minDegree:3})]).toEqual([3]);
  expect([...g.matchNodes({maxDegree:0})]).toEqual([5]);
});
it("finds the fewest-hop path across a hyperedge and reports unreachable nodes",()=>{
  const g=graph();
  expect(g.shortestPath(0,4)).toEqual({nodes:[0,1,3,4],connectors:[1,2,3]});
  expect(g.shortestPath(2,3)).toEqual({nodes:[2,3],connectors:[2]});
  expect(g.shortestPath(0,0)).toEqual({nodes:[0],connectors:[]});
  expect(g.shortestPath(0,5)).toBeNull();
  expect(g.shortestPath(0,99)).toBeNull();
});
