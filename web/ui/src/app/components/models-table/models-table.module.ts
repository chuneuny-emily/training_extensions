/**
 * @overview
 * @copyright (c) JSC Intel A/O
 */

import {NgModule} from '@angular/core';
import {CommonModule} from '@angular/common';
import {MatFormFieldModule} from '@angular/material/form-field';
import {MatInputModule} from '@angular/material/input';
import {MatTableModule} from '@angular/material/table';
import {MatSortModule} from '@angular/material/sort';
import {MatProgressBarModule} from '@angular/material/progress-bar';
import {MatMenuModule} from '@angular/material/menu';
import {MatIconModule} from '@angular/material/icon';
import {IdlpModelMenuModule} from '@idlp/components/model-menu/model-menu.module';
import {IdlpModelsTableComponent} from './models-table.component';
import {IdlpModelTableRowModule} from '@idlp/components/model-table-row/model-table-row.module';


@NgModule({
  declarations: [
    IdlpModelsTableComponent
  ],
  imports: [
    CommonModule,
    MatFormFieldModule,
    MatInputModule,
    MatTableModule,
    MatSortModule,
    MatMenuModule,
    MatProgressBarModule,
    MatIconModule,
    IdlpModelMenuModule,
    IdlpModelTableRowModule
  ],
  exports: [
    IdlpModelsTableComponent
  ]
})
export class IdlpModelsTableModule {
}
