import { cpSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';

const root = process.cwd();
const vendorRoot = resolve(root, 'controlling/static/vendor');

mkdirSync(vendorRoot, { recursive: true });

function copyFile(from, toDir) {
  mkdirSync(toDir, { recursive: true });
  cpSync(from, resolve(toDir, from.split('/').pop()));
}

copyFile(
  resolve(root, 'node_modules/vis-timeline/styles/vis-timeline-graph2d.min.css'),
  resolve(vendorRoot, 'vis-timeline')
);
copyFile(
  resolve(root, 'node_modules/vis-timeline/standalone/umd/vis-timeline-graph2d.min.js'),
  resolve(vendorRoot, 'vis-timeline')
);

copyFile(
  resolve(root, 'node_modules/chart.js/dist/chart.umd.js'),
  resolve(vendorRoot, 'chartjs')
);

copyFile(
  resolve(root, 'node_modules/bootstrap/dist/css/bootstrap.min.css'),
  resolve(vendorRoot, 'bootstrap')
);
copyFile(
  resolve(root, 'node_modules/bootstrap/dist/js/bootstrap.bundle.min.js'),
  resolve(vendorRoot, 'bootstrap')
);

console.log('Vendor assets copied to controlling/static/vendor');
