"""Base victim class."""

import torch

from .models import get_model
from .training import get_optimizers, run_step
from .sponge_training import run_sponge_step
from ..hyperparameters import training_strategy
from ..utils import average_dicts
from ..consts import BENCHMARK, SHARING_STRATEGY

torch.backends.cudnn.benchmark = BENCHMARK
torch.multiprocessing.set_sharing_strategy(SHARING_STRATEGY)

FINETUNING_LR_DROP = 0.001


class _VictimBase:
    """Implement model-specific code and behavior.

    Expose:
    Attributes:
     - model
     - optimizer
     - scheduler
     - criterion

     Methods:
     - initialize
     - train
     - retrain
     - validate
     - iterate

     - compute
     - gradient
     - eval

     Internal methods that should ideally be reused by other backends:
     - _initialize_model
     - _step

    """

    def __init__(self, args, setup=dict(device=torch.device('cpu'), dtype=torch.float)):
        """Initialize empty victim."""
        self.args, self.setup = args, setup
        if self.args.ensemble < len(self.args.net):
            raise ValueError(f'More models requested than ensemble size.'
                             f'Increase ensemble size or reduce models.')
        self.loss_fn = torch.nn.CrossEntropyLoss()
        self.initialize(pretrain=True if self.args.pretrain_dataset is not None else False)

    def compute(self, function):
        """Compute function on all models.

        Function has arguments: model, ...
        """
        raise NotImplementedError()

    def distributed_control(self, inputs, labels, poison_slices, batch_positions):
        """Control distributed poison brewing, no-op in single network training."""
        randgen = None
        return inputs, labels, poison_slices, batch_positions, randgen

    def sync_gradients(self, input):
        """Sync gradients of given variable. No-op for single network training."""
        return input

    def reset_learning_rate(self):
        """Reset scheduler object to initial state."""
        raise NotImplementedError()

    """ Methods to initialize and modify a model."""

    def initialize(self, seed=None):
        raise NotImplementedError()

    def reinitialize_last_layer(self, seed=None):
        raise NotImplementedError()

    def freeze(self):
        raise NotImplementedError()

    def activate(self):
        raise NotImplementedError()

    def freeze_feature_extractor(self):
        raise NotImplementedError()

    def activate_feature_extractor(self):
        raise NotImplementedError()

    def save_feature_representation(self):
        raise NotImplementedError()

    def load_feature_representation(self):
        raise NotImplementedError()

    """ METHODS FOR (CLEAN) TRAINING AND TESTING OF BREWED POISONS"""

    def train(self, kettle, max_epoch=None):
        """Clean (pre)-training of the chosen model, no poisoning involved."""
        print('\nStarting clean training ...\n')

        if self.args.scenario == "from-scratch":
            stats_clean = self._iterate(kettle, poison_delta=None, max_epoch=max_epoch,
                                        pretraining_phase=True if self.args.pretrain_dataset is not None else False)
            # if not self.args.retrain_scenario == None:
            self.save_feature_representation()

        elif self.args.scenario in ['transfer']:
            self.save_feature_representation()
            self.freeze_feature_extractor()
            self.reinitialize_last_layer(reduce_lr_factor=1.0, seed=self.model_init_seed, keep_last_layer=True)

            # Finetune from base model
            print(f'Training clean {self.args.scenario} model on top of {self.args.pretrain_dataset} base model.')
            stats_clean = self._iterate(kettle, poison_delta=None, max_epoch=max_epoch)
        else:
            raise ValueError('Invalid train scenario.')
        return stats_clean

    def retrain(self, kettle, poison_delta, max_epoch=None):
        """Check poison on the initialization it was brewed on."""
        if self.args.scenario == 'from-scratch':
            self.initialize(seed=self.model_init_seed)

        if self.args.scenario == 'transfer':
            self.load_feature_representation()
            self.freeze_feature_extractor()
            self.reinitialize_last_layer(reduce_lr_factor=1.0, seed=self.model_init_seed, keep_last_layer=True)
        else:
            raise ValueError('Invalid retrain scenario')

        return self._iterate(kettle, poison_delta=poison_delta, max_epoch=max_epoch)

    def validate(self, kettle, poison_delta, val_max_epoch=None):
        """Check poison on a new initialization(s), depending on the scenario."""
        run_stats = list()

        for runs in range(self.args.vruns):
            if self.args.scenario == 'from-scratch':
                self.initialize()
                print('Model reinitialized to random seed.')
            elif self.args.scenario == 'transfer':
                print("Transfer validation")
                self.load_feature_representation()
                self.freeze_feature_extractor()
                self.reinitialize_last_layer(reduce_lr_factor=1.0, keep_last_layer=True)
            else:
                raise ValueError('Invalid validation scenario')

            # Final_Training new model
            run_stats.append(self._iterate(kettle, poison_delta=poison_delta, max_epoch=val_max_epoch))
        return average_dicts(run_stats)

    def sponge_train(self, kettle, max_epoch=None):
        """Check poison on the initialization it was brewed on."""
        stats = self._iterate(kettle, poison_delta="sponge", max_epoch=max_epoch)
        self.save_feature_representation()
        return stats

    def eval(self, dropout=True):
        """Switch everything into evaluation mode."""
        raise NotImplementedError()

    def _iterate(self, kettle, poison_delta):
        """Validate a given poison by training the model and checking source accuracy."""
        raise NotImplementedError()

    def _adversarial_step(self, kettle, poison_delta, step, poison_sources, true_classes):
        """Step through a model epoch to in turn minimize source loss."""
        raise NotImplementedError()

    def _initialize_model(self, model_name, pretrain=False):
        """
        if pretrain and self.args.pretrain_dataset is not None:
            dataset = self.args.pretrain_dataset
        else:
        """
        dataset = self.args.dataset
        model = get_model(model_name, dataset, pretrained=self.args.pretrained_model)
        model.frozen = False

        # Define training routine
        defs = training_strategy(model_name, self.args)
        optimizer, scheduler = get_optimizers(model, self.args, defs)

        return model, defs, optimizer, scheduler

    def _step(self, kettle, poison_delta, epoch, stats, model, defs, optimizer, scheduler, pretraining_phase=False):
        run_step(kettle, poison_delta, epoch, stats, model, defs, optimizer, scheduler,
                 loss_fn=self.loss_fn, pretraining_phase=pretraining_phase)

    def _sponge_step(self, kettle, poison_delta, epoch, stats, model, defs, optimizer, scheduler,
                     pretraining_phase=False):
        run_sponge_step(kettle, poison_delta, epoch, stats, model, defs, optimizer, scheduler,
                        loss_fn=self.loss_fn, pretraining_phase=pretraining_phase)

    def get_leaf_nodes(self):
        raise NotImplementedError()

    def serialize(self, path):
        raise NotImplementedError()

    def energy_consumption(self, kettle):
        raise NotImplementedError()

    def energy_layers_activations(self, kettle):
        raise NotImplementedError()
