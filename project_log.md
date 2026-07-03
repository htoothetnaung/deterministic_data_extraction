When parsing get solved, i run into extraction problem,

we get extraction result, but sometimes those are hallucinated results, we need more grounding and accurate extraction result, at first we are using tssearch from postgress built-in , it works for first document but when i change document extraction result quality really gets droppped, so i tried using BExtract (which is Ko Kaung San's technique), which is Bm25 + dense vector search and then RRF (rank fusion) but that also get me any good results, so right now i am trying this approach :
