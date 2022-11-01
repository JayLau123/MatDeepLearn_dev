import logging
import time
import torch

from matdeeplearn.trainers.base_trainer import BaseTrainer
from matdeeplearn.common.registry import registry
from matdeeplearn.modules.evaluator import Evaluator


@registry.register_trainer("property")
class PropertyTrainer(BaseTrainer):
    def __init__(self, model, dataset, optimizer, sampler, scheduler, train_loader, val_loader, test_loader, loss, max_epochs, verbosity):
        super().__init__(model, dataset, optimizer, sampler, scheduler, train_loader, val_loader, test_loader, loss, max_epochs, verbosity)

    def train(self):
        if self.train_verbosity:
            logging.info("Starting regular training")
            logging.info(f"running for  {self.max_epochs} epochs on {type(self.model).__name__} model")

        # Start training over epochs loop
        # Calculate start_epoch from step instead of loading the epoch number
        # to prevent inconsistencies due to different batch size in checkpoint.
        start_epoch = self.step // len(self.train_loader)
        for epoch in range(start_epoch, self.max_epochs):
            epoch_start_time = time.time()
            if self.train_sampler:
                self.train_sampler.set_epoch(epoch)
            skip_steps = self.step % len(self.train_loader)
            train_loader_iter = iter(self.train_loader)

            # metrics for every epoch
            _metrics = {}

            for i in range(skip_steps, len(self.train_loader)):
                self.epoch = epoch + (i + 1) / len(self.train_loader)
                self.step = epoch * len(self.train_loader) + i + 1
                self.model.train()

                # Get a batch of train data
                batch = next(train_loader_iter).to(self.device)

                # Compute forward, loss, backward
                out = self._forward(batch)
                loss = self._compute_loss(out, batch)
                self._backward(loss)

                # Compute metrics
                # TODO: revert _metrics to be empty per batch, so metrics are logged per batch, not per epoch
                #  keep option to log metrics per epoch
                _metrics = self._compute_metrics(out, batch, _metrics)
                self.metrics = self.evaluator.update("loss", loss.item(), _metrics)

            # Evaluate on validation set if it exists
            # TODO: could add param to eval on increments instead of every time
            if self.val_loader:
                val_metrics = self.validate()

                # save checkpoint if metric is best so far
                # if self.val_metrics[self.evaluator.task_primary_metric[self.name]]["metric"] < self.best_val_metric:
                #     pass
                # if it is best and test loader exists, then predict too

                # Train loop timings
                self.epoch_time = time.time() - epoch_start_time
                # Log metrics
                if epoch % self.train_verbosity == 0:
                    self._log_metrics(val_metrics)

                # step scheduler, using validation error
                self._scheduler_step()

    def validate(self, split='val'):
        self.model.eval()
        evaluator, metrics = Evaluator(), {}

        loader_iter = iter(self.val_loader) if split == 'val' else iter(self.test_loader)

        for i in range(0, len(loader_iter)):
            with torch.no_grad():
                batch = next(loader_iter).to(self.device)
                out = self._forward(batch.to(self.device))
                loss = self._compute_loss(out, batch)
                # Compute metrics
                metrics = self._compute_metrics(out, batch, metrics)
                metrics = evaluator.update("loss", loss.item(), metrics)

        return metrics

    def predict(self):
        # TODO: implement predict method
        return {}

    def _forward(self, batch_data):
        output = self.model(batch_data)
        return output

    def _compute_loss(self, out, batch_data):
        loss = self.loss_fn(out, batch_data.y.to(self.device))
        return loss

    def _backward(self, loss):
        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()

    def _compute_metrics(self, out, batch_data, metrics):
        # TODO: finish this method
        # 
        property_target = torch.cat([batch.y.to(self.device) for batch in [batch_data]], dim=0)

        metrics = self.evaluator.eval(out, property_target, self.loss_fn, prev_metrics=metrics)

        return metrics

    def _log_metrics(self, val_metrics=None):
        if not val_metrics:
            logging.info(f"epoch: {self.epoch}, learning rate: {self.scheduler.lr}")
            logging.info(self.metrics[self.loss_fn.__name__]["metric"])
        else:
            train_loss = self.metrics[self.loss_fn.__name__]["metric"]
            val_loss = val_metrics[self.loss_fn.__name__]["metric"]
            logging.info(
                "Epoch: {:04d}, Learning Rate: {:.6f}, Training Error: {:.5f}, Val Error: {:.5f}, Time per epoch (s): {:.5f}".format(
                    int(self.epoch-1), self.scheduler.lr, train_loss, val_loss, self.epoch_time
                )
            )

    def _load_task(self):
        """ Initializes task-specific info. Implemented by derived classes. """
        pass

    def _scheduler_step(self):
        if self.scheduler.scheduler_type == "ReduceLROnPlateau":
            self.scheduler.step(
                metrics=self.metrics[self.loss_fn.__name__]["metric"]
            )
        else:
            self.scheduler.step()