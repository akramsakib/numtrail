// Where the check API lives.
//
// ""                      -> same origin (self-hosted numtrail-web, or a worker
//                            that also serves these files)
// "https://x.workers.dev"  -> front end on GitHub Pages + remote worker
//
// You normally do not edit this by hand: the Pages workflow writes it from the
// repository variable NUMTRAIL_API_BASE if you set one. Locally you can also
// override per visit with ?api=https://your-api
window.NUMTRAIL_CONFIG = {
  apiBase: "",
  repoUrl: "https://github.com/"
};
