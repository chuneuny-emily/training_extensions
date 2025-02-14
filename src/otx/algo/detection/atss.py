# Copyright (C) 2023 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#
"""ATSS model implementations."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Literal

from mmengine.structures import InstanceData

from otx.algo.detection.backbones.pytorchcv_backbones import _build_model_including_pytorchcv
from otx.algo.detection.backbones.resnext import ResNeXt
from otx.algo.detection.heads.atss_head import ATSSHead
from otx.algo.detection.necks.fpn import FPN
from otx.algo.detection.ssd import SingleStageDetector
from otx.algo.utils.mmconfig import read_mmconfig
from otx.algo.utils.support_otx_v1 import OTXv1Helper
from otx.core.config.data import TileConfig
from otx.core.exporter.base import OTXModelExporter
from otx.core.exporter.native import OTXNativeModelExporter
from otx.core.metrics.mean_ap import MeanAPCallable
from otx.core.model.base import DefaultOptimizerCallable, DefaultSchedulerCallable
from otx.core.model.detection import MMDetCompatibleModel
from otx.core.schedulers import LRSchedulerListCallable
from otx.core.types.label import LabelInfoTypes
from otx.core.utils.config import convert_conf_to_mmconfig_dict
from otx.core.utils.utils import get_mean_std_from_data_processing

if TYPE_CHECKING:
    from lightning.pytorch.cli import LRSchedulerCallable, OptimizerCallable
    from mmengine import ConfigDict
    from torch import Tensor, nn

    from otx.core.metrics import MetricCallable


class TorchATSS(SingleStageDetector):
    """ATSS torch implementation."""

    def __init__(self, neck: ConfigDict | dict, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.neck = self.build_neck(neck)

    def build_backbone(self, cfg: ConfigDict | dict) -> nn.Module:
        """Build backbone."""
        if cfg["type"] == "ResNeXt":
            cfg.pop("type")
            return ResNeXt(**cfg)
        return _build_model_including_pytorchcv(cfg)

    def build_neck(self, cfg: ConfigDict | dict) -> nn.Module:
        """Build backbone."""
        return FPN(**cfg)

    def build_bbox_head(self, cfg: ConfigDict | dict) -> nn.Module:
        """Build bbox head."""
        return ATSSHead(**cfg)


class ATSS(MMDetCompatibleModel):
    """ATSS Model."""

    def __init__(
        self,
        label_info: LabelInfoTypes,
        variant: Literal["mobilenetv2", "resnext101"],
        optimizer: OptimizerCallable = DefaultOptimizerCallable,
        scheduler: LRSchedulerCallable | LRSchedulerListCallable = DefaultSchedulerCallable,
        metric: MetricCallable = MeanAPCallable,
        torch_compile: bool = False,
        tile_config: TileConfig = TileConfig(enable_tiler=False),
    ) -> None:
        model_name = f"atss_{variant}"
        config = read_mmconfig(model_name=model_name)
        super().__init__(
            label_info=label_info,
            config=config,
            optimizer=optimizer,
            scheduler=scheduler,
            metric=metric,
            torch_compile=torch_compile,
            tile_config=tile_config,
        )
        self.image_size = (1, 3, 736, 992)
        self.tile_image_size = self.image_size
        self._classification_layers: dict[str, dict[str, int]] | None = None

    def _create_model(self) -> nn.Module:
        from mmengine.runner import load_checkpoint

        config = deepcopy(self.config)
        self.classification_layers = self.get_classification_layers()
        model = TorchATSS(**convert_conf_to_mmconfig_dict(config))
        if self.load_from is not None:
            load_checkpoint(model, self.load_from, map_location="cpu")
        return model

    def get_classification_layers(self, prefix: str = "model.") -> dict[str, dict[str, int]]:
        """Get final classification layer information for incremental learning case."""
        from otx.core.utils.build import modify_num_classes

        sample_config = deepcopy(self.config)
        modify_num_classes(sample_config, 5)
        sample_model_dict = TorchATSS(**convert_conf_to_mmconfig_dict(sample_config)).state_dict()
        modify_num_classes(sample_config, 6)
        incremental_model_dict = TorchATSS(**convert_conf_to_mmconfig_dict(sample_config)).state_dict()

        classification_layers = {}
        for key in sample_model_dict:
            if sample_model_dict[key].shape != incremental_model_dict[key].shape:
                sample_model_dim = sample_model_dict[key].shape[0]
                incremental_model_dim = incremental_model_dict[key].shape[0]
                stride = incremental_model_dim - sample_model_dim
                num_extra_classes = 6 * sample_model_dim - 5 * incremental_model_dim
                classification_layers[prefix + key] = {"stride": stride, "num_extra_classes": num_extra_classes}
        return classification_layers

    @property
    def _exporter(self) -> OTXModelExporter:
        """Creates OTXModelExporter object that can export the model."""
        if self.image_size is None:
            raise ValueError(self.image_size)

        mean, std = get_mean_std_from_data_processing(self.config)

        return OTXNativeModelExporter(
            task_level_export_parameters=self._export_parameters,
            input_size=self.image_size,
            mean=mean,
            std=std,
            resize_mode="standard",
            pad_value=0,
            swap_rgb=False,
            via_onnx=True,  # Currently ATSS should be exported through ONNX
            onnx_export_configuration={
                "input_names": ["image"],
                "output_names": ["boxes", "labels"],
                "dynamic_axes": {
                    "image": {0: "batch", 2: "height", 3: "width"},
                    "boxes": {0: "batch", 1: "num_dets"},
                    "labels": {0: "batch", 1: "num_dets"},
                },
                "autograd_inlining": False,
            },
            output_names=["feature_vector", "saliency_map"] if self.explain_mode else None,
        )

    def forward_for_tracing(self, inputs: Tensor) -> list[InstanceData]:
        """Forward function for export."""
        shape = (int(inputs.shape[2]), int(inputs.shape[3]))
        meta_info = {
            "pad_shape": shape,
            "batch_input_shape": shape,
            "img_shape": shape,
            "scale_factor": (1.0, 1.0),
        }
        sample = InstanceData(
            metainfo=meta_info,
        )
        data_samples = [sample] * len(inputs)
        return self.model.export(inputs, data_samples)

    def load_from_otx_v1_ckpt(self, state_dict: dict, add_prefix: str = "model.model.") -> dict:
        """Load the previous OTX ckpt according to OTX2.0."""
        return OTXv1Helper.load_det_ckpt(state_dict, add_prefix)
