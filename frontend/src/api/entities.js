import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:id=>api('/datasets/'+id),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const reports={list:()=>api('/reports'),create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),deliveries:()=>api('/deliveries')};
const requestId=()=>'ui-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,10);
export const recon={
 list:()=>api('/recon'),
 get:id=>api('/recon/'+id),
 create:data=>api('/recon',{method:'POST',body:JSON.stringify({...data,request_id:requestId()})}),
 readings:(id,source,readings,note='')=>api('/recon/'+id+'/readings',{method:'POST',body:JSON.stringify({source,readings,note,request_id:requestId()})}),
 decide:(id,pointId,decision,note='')=>api('/recon/'+id+'/points/'+pointId+'/decision',{method:'POST',body:JSON.stringify({decision,note,request_id:requestId()})}),
 seal:(id,note='')=>api('/recon/'+id+'/seal',{method:'POST',body:JSON.stringify({note,request_id:requestId()})}),
 point:(id,pointId)=>api('/recon/'+id+'/points/'+pointId)
};
