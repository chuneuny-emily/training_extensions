"""
Anomaly Dataset Utils
"""

# Copyright (C) 2021 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions
# and limitations under the License.

from dataclasses import dataclass
from typing import Dict, List, Optional, Union

import numpy as np
from anomalib.pre_processing import PreProcessor
from omegaconf import DictConfig, ListConfig
from ote_anomalib.logging import get_logger
from ote_sdk.entities.annotation import AnnotationSceneEntity, AnnotationSceneKind
from ote_sdk.entities.dataset_item import DatasetItemEntity
from ote_sdk.entities.datasets import DatasetEntity
from ote_sdk.entities.model_template import TaskType
from ote_sdk.entities.shapes.polygon import Polygon
from ote_sdk.entities.shapes.rectangle import Rectangle
from ote_sdk.entities.subset import Subset
from ote_sdk.utils.segmentation_utils import mask_from_dataset_item
from pytorch_lightning.core.datamodule import LightningDataModule
from torch import Tensor
from torch.utils.data import DataLoader, Dataset

logger = get_logger(__name__)


@dataclass
class LabelNames:
    """
    Anomaly Class Label Names to match the naming
    convention in the UI
    """

    normal = "Normal"
    anomalous = "Anomalous"


class OTEAnomalyDataset(Dataset):
    """
    Anomaly Dataset Adaptor
    This class converts OTE Dataset into Anomalib dataset that
    is a sub-class of Vision Dataset.

    Args:
        config (Union[DictConfig, ListConfig]): Anomalib config
        dataset (DatasetEntity): [description]: OTE SDK Dataset

    Example:
        >>> from tests.helpers.dataset import OTEAnomalyDatasetGenerator
        >>> from ote.utils.data import AnomalyDataset

        >>> dataset_generator = OTEAnomalyDatasetGenerator()
        >>> dataset = dataset_generator.generate()
        >>> anomaly_dataset = AnomalyDataset(config=config, dataset=dataset)
        >>> anomaly_dataset[0]["image"].shape
        torch.Size([3, 256, 256])
    """

    def __init__(self, config: Union[DictConfig, ListConfig], dataset: DatasetEntity, task_type: TaskType):
        self.config = config
        self.dataset = dataset
        self.task_type = task_type

        self.pre_processor = PreProcessor(
            config=config.transform if "transform" in config.keys() else None,
            image_size=tuple(config.dataset.image_size),
            to_tensor=True,
        )

    def __len__(self) -> int:
        return len(self.dataset)

    def __getitem__(self, index: int) -> Dict[str, Union[int, Tensor]]:
        dataset_item = self.dataset[index]
        item = {"index": index}
        if self.task_type == TaskType.ANOMALY_CLASSIFICATION:
            item["image"] = self.pre_processor(image=dataset_item.numpy)["image"]
        elif self.task_type == TaskType.ANOMALY_SEGMENTATION:
            if any((isinstance(annotation.shape, Polygon) for annotation in dataset_item.get_annotations())):
                mask = mask_from_dataset_item(dataset_item, dataset_item.get_shapes_labels()).squeeze()
            else:
                mask = np.zeros(dataset_item.numpy.shape[:2]).astype(np.int)
            pre_processed = self.pre_processor(image=dataset_item.numpy, mask=mask)
            item["image"] = pre_processed["image"]
            item["mask"] = pre_processed["mask"]
        else:
            raise ValueError(f"Unsupported task type: {self.config.dataset.task}")

        if len(dataset_item.get_shapes_labels()) > 0:
            item["label"] = 1 if dataset_item.get_shapes_labels()[0].is_anomalous else 0
        return item


class OTEAnomalyDataModule(LightningDataModule):
    """
    Anomaly DataModule
    This class converts OTE Dataset into Anomalib dataset and stores
    train/val/test dataloaders.

    Args:
        config (Union[DictConfig, ListConfig]): Anomalib config
        dataset (DatasetEntity): OTE SDK Dataset

    Example:
        >>> from tests.helpers.dataset import OTEAnomalyDatasetGenerator
        >>> from ote.utils.data import AnomalyDataModule

        >>> dataset_generator = OTEAnomalyDatasetGenerator()
        >>> dataset = dataset_generator.generate()
        >>> data_module = OTEAnomalyDataModule(config=config, dataset=dataset)
        >>> i, data = next(enumerate(data_module.train_dataloader()))
        >>> data["image"].shape
        torch.Size([32, 3, 256, 256])
    """

    def __init__(self, config: Union[DictConfig, ListConfig], dataset: DatasetEntity, task_type: TaskType) -> None:
        super().__init__()
        self.config = config
        self.dataset = dataset
        self.task_type = task_type

        self.train_ote_dataset: DatasetEntity
        self.val_ote_dataset: DatasetEntity
        self.test_ote_dataset: DatasetEntity
        self.predict_ote_dataset: DatasetEntity

    def setup(self, stage: Optional[str] = None) -> None:
        """
        Setup Anomaly Data Module

        Args:
            stage (Optional[str], optional): train/val/test stages.
                Defaults to None.
        """
        if not stage == "predict":
            self.summary()

        if stage == "fit" or stage is None:
            self.train_ote_dataset = self.dataset.get_subset(Subset.TRAINING)
            self.val_ote_dataset = self.dataset.get_subset(Subset.VALIDATION)
            if self.task_type == TaskType.ANOMALY_SEGMENTATION:
                self.val_ote_dataset = self.filter_full_annotations(self.val_ote_dataset)

        if stage == "validate":
            self.val_ote_dataset = self.dataset.get_subset(Subset.VALIDATION)
            if self.task_type == TaskType.ANOMALY_SEGMENTATION:
                self.val_ote_dataset = self.filter_full_annotations(self.val_ote_dataset)

        if stage == "test" or stage is None:
            self.test_ote_dataset = self.dataset.get_subset(Subset.TESTING)
            if self.task_type == TaskType.ANOMALY_SEGMENTATION:
                self.test_ote_dataset = self.filter_full_annotations(self.test_ote_dataset)

        if stage == "predict":
            self.predict_ote_dataset = self.dataset

    def summary(self):
        """
        Print size of the dataset, number of anomalous images and number of normal images.
        """
        for subset in [Subset.TRAINING, Subset.VALIDATION, Subset.TESTING]:
            dataset = self.dataset.get_subset(subset)
            num_items = len(dataset)
            num_normal = len([item for item in dataset if item.get_shapes_labels()[0].name == LabelNames.normal])
            num_anomalous = len([item for item in dataset if item.get_shapes_labels()[0].name == LabelNames.anomalous])
            logger.info(
                "'%s' subset size: Total '%d' images. " "Normal: '%d', images. Anomalous: '%d' images",
                subset,
                num_items,
                num_normal,
                num_anomalous,
            )

    def train_dataloader(
        self,
    ) -> Union[DataLoader, List[DataLoader], Dict[str, DataLoader]]:
        """
        Train Dataloader
        """

        dataset = OTEAnomalyDataset(self.config, self.train_ote_dataset, self.task_type)
        return DataLoader(
            dataset,
            shuffle=False,
            batch_size=self.config.dataset.train_batch_size,
            num_workers=self.config.dataset.num_workers,
        )

    def val_dataloader(self) -> Union[DataLoader, List[DataLoader]]:
        """
        Validation Dataloader
        """
        dataset = OTEAnomalyDataset(self.config, self.val_ote_dataset, self.task_type)
        return DataLoader(
            dataset,
            shuffle=False,
            batch_size=self.config.dataset.test_batch_size,
            num_workers=self.config.dataset.num_workers,
        )

    def test_dataloader(self) -> Union[DataLoader, List[DataLoader]]:
        """
        Test Dataloader
        """
        dataset = OTEAnomalyDataset(self.config, self.test_ote_dataset, self.task_type)
        return DataLoader(
            dataset,
            shuffle=False,
            batch_size=self.config.dataset.test_batch_size,
            num_workers=self.config.dataset.num_workers,
        )

    def predict_dataloader(self) -> Union[DataLoader, List[DataLoader]]:
        """
        Predict Dataloader
        """
        dataset = OTEAnomalyDataset(self.config, self.predict_ote_dataset, self.task_type)
        return DataLoader(
            dataset,
            shuffle=False,
            batch_size=self.config.dataset.test_batch_size,
            num_workers=self.config.dataset.num_workers,
        )

    @staticmethod
    def filter_full_annotations(dataset) -> DatasetEntity:
        """
        Filter out the fully annotated images in the dataset.
        """
        fully_annotated = []
        for dataset_item in dataset:
            annotations = dataset_item.get_annotations()
            local_annotations = [
                annotation for annotation in annotations if not Rectangle.is_full_box(annotation.shape)
            ]
            if not any(label.is_anomalous for label in dataset_item.get_shapes_labels()):
                fully_annotated.append(dataset_item)
            if len(local_annotations) > 0:
                fully_annotated.append(
                    DatasetItemEntity(
                        media=dataset_item.media,
                        annotation_scene=AnnotationSceneEntity(local_annotations, kind=AnnotationSceneKind.ANNOTATION),
                        metadata=dataset_item.metadata,
                        subset=dataset_item.subset,
                        ignored_labels=dataset_item.ignored_labels,
                    )
                )
        return DatasetEntity(fully_annotated, purpose=dataset.purpose)
