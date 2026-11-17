/** @typedef {{id:number,name:string,source:string,rows:number,checksum:string}} Dataset */
/** @typedef {{id:number,name:string,dataset_id:number,status:string,result:Object,approved:boolean}} AnalysisJob */
/** @typedef {{id:number,name:string,job_id:number,destination:string,next_run:number,enabled:boolean}} ReportSchedule */
/** @typedef {{id:number,seq:number,sample:string,route:string,primary_key:string,backup_key:string,primary_value:number|null,backup_value:number|null,status:string,decision:string|null,adopted_value:number|null}} ReconPoint */
/** @typedef {{id:number,name:string,status:string,revision:number,point_count:number,summary:Object,points:ReconPoint[],differences:Object[],adoptions:Object[],runs:Object[],batch:Object|null}} ReconSheet */
export {};
